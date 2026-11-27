"""Modern PySide6 calibration workspace for FragEngine."""
from __future__ import annotations
from pathlib import Path
import sys
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QFileDialog, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget
from ..core import config as config_manager
from ..core import paths
from ..core import picker as native_picker
from . import theme

ROOT=paths.PROJECT_ROOT
ACCENT=QColor(theme.ACCENT); TEMPLATE=QColor(theme.GREEN)
STYLE=""  # styling comes from theme.apply()

class Canvas(QGraphicsView):
    def __init__(self):
        super().__init__();self.scene_=QGraphicsScene(self);self.setScene(self.scene_);self.setObjectName("canvas");self.setBackgroundBrush(QColor(theme.SURFACE));self.hint=self.scene_.addText("Load a full-size gameplay screenshot to begin");self.hint.setDefaultTextColor(QColor(theme.TEXT_3));self.hint.setFont(theme.font(10));self.setRenderHints(QPainter.RenderHint.Antialiasing|QPainter.RenderHint.SmoothPixmapTransform);self.start=None;self.selection=None;self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
    def set_image(self,image):
        self.scene_.clear();self.scene_.addItem(QGraphicsPixmapItem(QPixmap.fromImage(image)));self.scene_.setSceneRect(image.rect());self.selection=None;self.fit()
    def fit(self):self.fitInView(self.scene_.sceneRect(),Qt.AspectRatioMode.KeepAspectRatio)
    def zoom(self,factor):self.scale(factor,factor)
    def begin(self,color,changed):
        self.color,self.changed=color,changed
    def set_selection(self, rect):
        if self.selection:self.scene_.removeItem(self.selection)
        self.selection=QGraphicsRectItem(QRectF(rect))
        self.selection.setPen(QPen(self.color,1.5));self.selection.setBrush(QColor(self.color.red(),self.color.green(),self.color.blue(),32));self.scene_.addItem(self.selection)
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.start=self.mapToScene(event.position().toPoint())
            if self.selection:self.scene_.removeItem(self.selection)
            self.selection=QGraphicsRectItem();self.selection.setPen(QPen(self.color,1.5));self.selection.setBrush(QColor(self.color.red(),self.color.green(),self.color.blue(),32));self.scene_.addItem(self.selection);return
        super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self.start and self.selection:
            self.selection.setRect(QRectF(self.start,self.mapToScene(event.position().toPoint())).normalized());self.changed(self.selection.rect());return
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        if self.start:self.start=None;self.changed(self.selection.rect());return
        super().mouseReleaseEvent(event)
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape and self.selection:self.scene_.removeItem(self.selection);self.selection=None;return
        super().keyPressEvent(event)

class Calibration(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle("FragEngine — Calibration");self.resize(1100,760);self.setMinimumSize(900,620);self.image=QImage();self.crop=None;self.template=None;self.last_saved_template=None;self.step=1;self._updating_fields=False;self.build();self.set_step(1);theme.pad_spinboxes(self)
    def build(self):
        root=QWidget();self.setCentralWidget(root);root.setObjectName("root");layout=QVBoxLayout(root);layout.setContentsMargins(22,18,22,18);layout.setSpacing(12);header=QHBoxLayout();title=QLabel("Calibration");title.setObjectName("h1");header.addWidget(title);header.addStretch();header.addWidget(QLabel("Configuration ●",objectName="muted"));layout.addLayout(header)
        self.steps=QLabel();self.steps.setStyleSheet(f"color:{theme.TEXT_2};padding:8px 0;");layout.addWidget(self.steps)
        body=QHBoxLayout();left=QVBoxLayout();tools=QHBoxLayout()
        for text,fn,tip in (("Load",self.load,"Load full-size screenshot"),("＋",lambda:self.canvas.zoom(1.25),"Zoom in"),("−",lambda:self.canvas.zoom(.8),"Zoom out"),("Fit",self.fit,"Fit image"),("Reset",self.reset_selection,"Clear selection")):
            b=QPushButton(text);b.clicked.connect(fn);b.setToolTip(tip);tools.addWidget(b)
        tools.addStretch();left.addLayout(tools);self.canvas=Canvas();left.addWidget(self.canvas,1);body.addLayout(left,1)
        panel=QFrame(objectName="panel");panel.setFixedWidth(340);right=QVBoxLayout(panel);right.setContentsMargins(14,14,14,14);right.setSpacing(7);self.instruction=QLabel();self.instruction.setWordWrap(True);self.coords=QLabel(objectName="muted");self.coords.setStyleSheet(f"font-family:'{theme.mono_family()}';font-size:9pt;");self.preview=QLabel("Preview");self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter);self.preview.setMinimumHeight(104);self.preview.setStyleSheet(f"background:{theme.BG};border:1px solid {theme.BORDER};border-radius:8px;")
        for item in (self.instruction,self.coords,self.preview):right.addWidget(item)
        self.crop_editor,self.crop_inputs=self.coordinate_editor("Kill Feed — original image pixels",self.apply_crop_inputs)
        self.template_editor,self.template_inputs=self.coordinate_editor("Template — kill-feed-relative pixels",self.apply_template_inputs)
        right.addWidget(self.crop_editor);right.addWidget(self.template_editor)
        self.filename=QLineEdit();self.filename.setPlaceholderText("Template filename");right.addWidget(self.filename)
        self.copy=QPushButton("Copy crop");self.copy.clicked.connect(self.copy_crop);right.addWidget(self.copy)
        for text,handler in (("Save Template",self.save_template),("Save Kill Feed Sample",self.save_sample),("Save Selection Preview",self.save_overlay)):
            button=QPushButton(text);button.clicked.connect(handler);right.addWidget(button)
        right.addStretch();body.addWidget(panel);layout.addLayout(body,1)
        footer=QHBoxLayout();self.back=QPushButton("← Back");self.back.clicked.connect(self.previous);self.next=QPushButton("Continue →",objectName="primary");self.next.clicked.connect(self.advance);footer.addWidget(self.back);footer.addStretch();footer.addWidget(self.next);layout.addLayout(footer)
    def coordinate_editor(self,title,handler):
        editor=QFrame();layout=QVBoxLayout(editor);layout.setContentsMargins(0,8,0,0);label=QLabel(title,objectName="muted");layout.addWidget(label);row=QHBoxLayout();inputs={}
        for name in ("X","Y","W","H"):
            column=QVBoxLayout();caption=QLabel(name,objectName="muted");field=QSpinBox();field.setKeyboardTracking(False);field.setRange(0,0);field.setToolTip(f"Manually adjust {name} in pixels.");field.valueChanged.connect(lambda _value, callback=handler: callback());column.addWidget(caption);column.addWidget(field);row.addLayout(column);inputs[name.lower()]=field
        layout.addLayout(row);return editor,inputs
    def set_step(self,step):
        self.step=step;labels=["01  Kill Feed","02  Template","03  Review","04  Save"];self.steps.setText("     ".join(("● "+x if i+1==step else "✓ "+x if i+1<step else "○ "+x) for i,x in enumerate(labels)))
        if step==1:
            self.instruction.setText("SELECT KILL FEED\nDrag over the complete kill feed, or use X/Y/W/H below for pixel-level adjustment.");self.next.setText("Continue →");self.canvas.begin(ACCENT,self.crop_changed)
            if not self.image.isNull():
                self.canvas.set_image(self.image)
                if self.crop:self.canvas.set_selection(self.crop)
        elif step==2:
            self.instruction.setText("SELECT TEMPLATE\nDrag over your name inside the Kill Feed crop, or use X/Y/W/H below for pixel-level adjustment.");self.next.setText("Continue →");self.canvas.begin(TEMPLATE,self.template_changed)
            if self.crop:
                self.canvas.set_image(self.image.copy(self.crop))
                if self.template:self.canvas.set_selection(self.template)
        elif step==3:self.instruction.setText("REVIEW\nVerify the kill-feed crop and template before saving.");self.next.setText("Continue →")
        else:self.instruction.setText("READY TO SAVE\nConfiguration: config/config.json\nTemplate: "+self.next_name());self.next.setText("Save Configuration")
        self.back.setEnabled(step>1);self.render_info()
    def load(self):
        path=native_picker.choose_image()
        if path is None:
            path,_=QFileDialog.getOpenFileName(self,"Load gameplay screenshot","","Images (*.png *.jpg *.jpeg *.bmp);;All files (*)")
        if not path:return
        self.image=QImage(path)
        if self.image.isNull():self.instruction.setText("Could not read image. Choose another screenshot.");return
        self.crop=self.template=None;self.last_saved_template=None;self.canvas.set_image(self.image);self.set_step(1);theme.pad_spinboxes(self)
    def fit(self):
        if not self.image.isNull():self.canvas.fit()
    def reset_selection(self):
        if self.canvas.selection:self.canvas.scene_.removeItem(self.canvas.selection);self.canvas.selection=None
        if self.step==1:self.crop=None
        elif self.step==2:self.template=None
        self.render_info()
    def crop_changed(self,rect):
        if self.image.isNull():return
        self.crop=rect.toAlignedRect().intersected(self.image.rect());self.render_info()
    def template_changed(self,rect):
        if not self.crop:return
        self.template=rect.toAlignedRect().intersected(self.image.copy(self.crop).rect());self.render_info()
    def set_coordinate_inputs(self,inputs,rect,bounds):
        self._updating_fields=True;active=bounds.width()>0 and bounds.height()>0
        limits={"x":max(0,bounds.width()-1),"y":max(0,bounds.height()-1),"w":bounds.width(),"h":bounds.height()}
        values={"x":rect.x(),"y":rect.y(),"w":rect.width(),"h":rect.height()} if rect else {"x":0,"y":0,"w":1 if active else 0,"h":1 if active else 0}
        for name,field in inputs.items():field.setRange(1 if active and name in {"w","h"} else 0,limits[name] if active else 0);field.setValue(values[name])
        self._updating_fields=False
    @staticmethod
    def rect_from_inputs(inputs,bounds):
        x=min(max(0,inputs["x"].value()),max(0,bounds.width()-1));y=min(max(0,inputs["y"].value()),max(0,bounds.height()-1))
        w=min(max(1,inputs["w"].value()),bounds.width()-x);h=min(max(1,inputs["h"].value()),bounds.height()-y)
        return QRect(x,y,w,h)
    def apply_crop_inputs(self):
        if self._updating_fields or self.image.isNull() or self.step!=1:return
        self.crop=self.rect_from_inputs(self.crop_inputs,self.image.rect());self.canvas.set_selection(self.crop);self.render_info()
    def apply_template_inputs(self):
        if self._updating_fields or not self.crop or self.step!=2:return
        bounds=self.image.copy(self.crop).rect();self.template=self.rect_from_inputs(self.template_inputs,bounds);self.canvas.set_selection(self.template);self.render_info()
    def render_info(self):
        self.crop_editor.setEnabled(not self.image.isNull() and self.step==1);self.template_editor.setEnabled(bool(self.crop) and self.step==2)
        self.set_coordinate_inputs(self.crop_inputs,self.crop,self.image.rect() if not self.image.isNull() else QRect())
        template_bounds=self.image.copy(self.crop).rect() if self.crop else QRect()
        self.set_coordinate_inputs(self.template_inputs,self.template,template_bounds)
        if self.image.isNull():self.coords.setText("No screenshot loaded\nLoad a full-size gameplay screenshot.");self.preview.setText("Preview");return
        source=f"Source\n{self.image.width()} × {self.image.height()}\n\n"
        if self.step==1 and self.crop:
            c=self.crop;self.coords.setText(source+f"SELECTION (original pixels)\nX  {c.x()}\nY  {c.y()}\nW  {c.width()}\nH  {c.height()}\n\nFFmpeg crop\ncrop={c.width()}:{c.height()}:{c.x()}:{c.y()}");self.set_preview(self.image.copy(c))
        elif self.step>=2 and self.crop:
            c=self.crop;text=source+f"KILL FEED\nX  {c.x()}\nY  {c.y()}\nW  {c.width()}\nH  {c.height()}"
            if self.template:
                t=self.template;text+=f"\n\nTEMPLATE (crop-relative)\nX  {t.x()}\nY  {t.y()}\nW  {t.width()}\nH  {t.height()}\n\nAbsolute\nX  {c.x()+t.x()}\nY  {c.y()+t.y()}";self.set_preview(self.image.copy(c.x()+t.x(),c.y()+t.y(),t.width(),t.height()))
            else:self.set_preview(self.image.copy(c))
            self.coords.setText(text)
        else:self.coords.setText(source+"Select the Kill Feed region.");self.preview.setText("Preview")
    def set_preview(self,image):
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(250,180,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
    def advance(self):
        if self.step==1:
            if not self.crop or self.crop.width()<1:self.instruction.setText("Select a valid Kill Feed region before continuing.");return
            self.set_step(2)
        elif self.step==2:
            if not self.template or not self.image.copy(self.crop).rect().contains(self.template):self.instruction.setText("Template must be completely inside the Kill Feed.");return
            self.set_step(3)
        elif self.step==3:self.set_step(4)
        else:self.save()
    def previous(self):self.set_step(max(1,self.step-1))
    def copy_crop(self):
        if self.crop:QApplication.clipboard().setText(f"crop={self.crop.width()}:{self.crop.height()}:{self.crop.x()}:{self.crop.y()}")
    def next_name(self):
        folder=paths.TEMPLATES_DIR;i=0
        while (folder/f"Capture{i}.PNG").exists():i+=1
        return f"assets/templates/Capture{i}.PNG"
    def save_template(self,show_message=True):
        if not self.crop or not self.template:
            self.instruction.setText("Select a template before saving.");return False
        name=self.filename.text().strip() or self.next_name().split("/")[-1]
        if Path(name).name!=name or not name.lower().endswith(".png"):
            self.instruction.setText("Template filename must be a PNG filename without folders.");return False
        folder=paths.TEMPLATES_DIR;folder.mkdir(parents=True, exist_ok=True);path=folder/name
        if path.exists():
            self.instruction.setText(f"{name} already exists; choose another filename.");return False
        t=self.template
        if not QPixmap.fromImage(self.image.copy(self.crop.x()+t.x(),self.crop.y()+t.y(),t.width(),t.height())).save(str(path),"PNG"):
            self.instruction.setText("Could not save template.");return False
        self.last_saved_template=path;self.filename.setText(self.next_name().split("/")[-1])
        if show_message:self.instruction.setText(f"Saved template: {path.name}")
        return True
    def save_sample(self):
        if not self.crop:self.instruction.setText("Select the Kill Feed before saving a sample.");return
        folder=paths.CALIBRATION_OUTPUT_DIR;folder.mkdir(parents=True, exist_ok=True);i=1
        while (folder/f"sample_{i:03d}.png").exists():i+=1
        QPixmap.fromImage(self.image.copy(self.crop)).save(str(folder/f"sample_{i:03d}.png"),"PNG");self.instruction.setText(f"Saved Kill Feed sample {i:03d}.")
    def save_overlay(self):
        if not self.crop:self.instruction.setText("Select the Kill Feed before saving a preview.");return
        image=self.image.copy();painter=QPainter(image);painter.setPen(QPen(ACCENT,3));painter.drawRect(self.crop)
        if self.template:
            painter.setPen(QPen(TEMPLATE,3));t=self.template;painter.drawRect(self.crop.x()+t.x(),self.crop.y()+t.y(),t.width(),t.height())
        painter.end();folder=paths.CALIBRATION_OUTPUT_DIR;folder.mkdir(parents=True, exist_ok=True);i=1
        while (folder/f"preview_{i:03d}.png").exists():i+=1
        image.save(str(folder/f"preview_{i:03d}.png"),"PNG");self.instruction.setText(f"Saved selection preview {i:03d}.")
    def save(self):
        if not self.crop:
            self.instruction.setText("Select a Kill Feed crop before saving configuration.");return
        try: config=config_manager.load()
        except (FileNotFoundError, ValueError): config=config_manager.default_config()
        config["source_resolution"]={"width":self.image.width(),"height":self.image.height()};config["kill_feed_crop"]={"x":self.crop.x(),"y":self.crop.y(),"width":self.crop.width(),"height":self.crop.height()}
        try:config_manager.save(config,backup=config_manager.CONFIG_PATH.exists())
        except Exception as error:self.instruction.setText(f"Configuration was not saved: {error}");return
        QMessageBox.information(self,"Calibration saved","Saved config/config.json. Templates are saved only with Save Template.");self.close()

def main():
    app=QApplication(sys.argv);theme.apply(app);window=Calibration();window.show();theme.dark_title_bar(window);return app.exec()
if __name__=="__main__":raise SystemExit(main())
