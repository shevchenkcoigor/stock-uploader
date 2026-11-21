"""Step 5: build the Adobe CSV, then submit in the portal (manual) and mark the batch."""

from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFormLayout, QMessageBox, QPushButton

from adobe_stock import services, state
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.task_page import TaskPage
from adobe_stock.gui.widgets import make_combo

PORTAL_STEPS = (
    "В портале Adobe Stock: Uploaded Files → New → Upload CSV. Затем перед отправкой на проверку отметьте "
    "галочки (список файлов в чеклисте). Загрузка CSV и отправка выполняются вручную."
)


class CsvPage(TaskPage):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            ctx,
            "CSV и отправка",
            "CSV включает только уже загруженные по SFTP файлы батча. Повторное создание перезаписывает "
            "тот же файл. Отправка на проверку в портале выполняется вами вручную.",
        )
        form = QFormLayout()
        self.batch = make_combo()
        form.addRow("Батч", self.batch)
        self.form.addLayout(form)
        self.create_button = self.add_action(QPushButton("Создать CSV"))
        self.create_button.clicked.connect(self._create)
        self.open_button = self.add_action(QPushButton("Открыть папку с CSV"))
        self.open_button.clicked.connect(self._open_folder)
        self.submitted_button = self.add_action(QPushButton("Отметить отправленным"))
        self.submitted_button.clicked.connect(self._submitted)
        self._csv_path: Path | None = None

    def refresh(self) -> None:
        rows = [r for r in self.ctx.read(state.status_summary) if r["status"] == state.UPLOADED]
        self.batch.clear()
        for row in rows:
            csv = "CSV есть" if row["csv_path"] else "CSV ещё нет"
            label = f"Батч {row['batch_id']}: {row['folder']}, часть {row['part']} — {row['n']} файлов, {csv}"
            self.batch.addItem(label, row["batch_id"])
        self.status.setText("" if rows else "Нет загруженных и неотправленных файлов")

    def _batch_id(self) -> int | None:
        data = self.batch.currentData()
        return int(data) if data is not None else None

    def _create(self) -> None:
        batch = self._batch_id()
        if batch is None:
            return
        try:
            settings = self.ctx.prefs.build_settings()
        except (ValidationError, ValueError) as error:
            self.write(f"Проверьте настройки: {error}")
            return

        def work(conn, progress, should_stop):
            return services.build_csv(conn, batch, settings)

        self.start(work, self._created)

    def _created(self, report: object) -> None:
        if not isinstance(report, services.CsvReport):
            return
        if report.empty:
            self.write("В батче нет загруженных и неотправленных файлов.")
        elif report.problems:
            for problem in report.problems:
                self.write(f"ПРОБЛЕМА CSV: {problem}")
        else:
            self._csv_path = report.path
            self.write(f"CSV: {report.path} ({report.rows} строк, проверен по правилам Adobe)")
            self.write(PORTAL_STEPS)
            self.write(f"  • «Created using generative AI tools»: все {report.rows} файлов (обязательно)")
            self.write(f"  • «People and Property are fictional»: {report.people} файлов с людьми")
            if report.notes and report.notes.exists():
                self.write(f"\nЧеклист ({report.notes}):\n" + report.notes.read_text(encoding="utf-8"))
        self.refresh()

    def _open_folder(self) -> None:
        folder = self._csv_path.parent if self._csv_path else self.ctx.prefs.csv_dir
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _submitted(self) -> None:
        batch = self._batch_id()
        if batch is None:
            return
        answer = QMessageBox.question(
            self,
            "Отметить отправленным",
            f"Вы уже загрузили CSV батча {batch} в портале и отправили файлы на проверку?\n"
            "Батч будет помечен как отправленный.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        def work(conn, progress, should_stop):
            return services.mark_submitted(conn, batch)

        self.start(work, self._marked)

    def _marked(self, report: object) -> None:
        if isinstance(report, services.SubmittedReport):
            for warning in report.warnings:
                self.write(f"ВНИМАНИЕ {warning}")
            self.write(f"Отмечено отправленными: {report.count}")
        self.refresh()
