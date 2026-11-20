"""Step 1: choose a folder and register its images."""

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QSpinBox, QWidget

from adobe_stock import services
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.task_page import TaskPage

MAX_INVALID_SHOWN = 30


class ScanPage(TaskPage):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            ctx,
            "Скан папки",
            "Находит JPEG в папке и во вложенных папках, проверяет их по правилам Adobe и делит новые "
            "файлы на батчи. Уже известные файлы пропускаются.",
        )
        form = QFormLayout()
        self.folder = QLineEdit(ctx.prefs.text("image_folder"))
        browse = QPushButton("Выбрать…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Папка с картинками", holder)
        self.batch_size = QSpinBox()
        self.batch_size.setRange(1, 20_000)
        self.batch_size.setValue(ctx.prefs.batch_size)
        form.addRow("Файлов в батче", self.batch_size)
        self.form.addLayout(form)
        self.run_button = self.add_action(QPushButton("Сканировать"))
        self.run_button.clicked.connect(self._run)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Папка с картинками", self.folder.text())
        if chosen:
            self.folder.setText(chosen)

    def _run(self) -> None:
        root = Path(self.folder.text().strip()).expanduser()
        if not root.is_dir():
            self.write(f"Папка не найдена: {root}")
            return
        self.ctx.prefs.set_text("image_folder", str(root))
        size = self.batch_size.value()
        self.ctx.prefs.set_text("batch_size", str(size))
        resolved = root.resolve()

        def work(conn, progress, should_stop):
            progress(0, 0, "Ищу JPEG…")
            images = services.find_images(resolved)
            if not images:
                return None
            progress(0, 0, f"Проверяю {len(images)} файлов…")
            return services.scan(conn, resolved, images, None, size)

        self.start(work, self._done)

    def _done(self, report: object) -> None:
        if not isinstance(report, services.ScanReport):
            self.write("JPEG-файлы не найдены.")
            self.status.setText("Нет файлов")
            return
        r = report.result
        self.write(
            f"Добавлено: {r.added}\nЖдут подписи: {r.needs_metadata}\nБез изменений: {r.unchanged}\n"
            f"Невалидных: {r.invalid}\nНовых батчей: {r.batches_created}"
        )
        for path, error in report.invalid[:MAX_INVALID_SHOWN]:
            self.write(f"НЕВАЛИДЕН {path}: {error}")
        for name in r.changed_after_upload:
            self.write(f"ВНИМАНИЕ {name} изменён после загрузки, повторно не грузится")
        if r.needs_metadata:
            self.write("Дальше: вкладка «Подписи».")
        self.status.setText("Готово")
