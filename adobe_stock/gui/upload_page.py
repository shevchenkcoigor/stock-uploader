"""Step 4: SFTP upload of a batch."""

from pydantic import ValidationError
from PySide6.QtWidgets import QComboBox, QFormLayout, QPushButton

from adobe_stock import services, state
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.task_page import TaskPage


class UploadPage(TaskPage):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            ctx,
            "Загрузка по SFTP",
            "Файлы батча загружаются в корень вашего SFTP на Adobe Stock. Можно отменить: уже загруженные "
            "файлы запоминаются, остальные останутся в очереди.",
        )
        form = QFormLayout()
        self.batch = QComboBox()
        form.addRow("Батч", self.batch)
        self.form.addLayout(form)
        self.run_button = self.add_action(QPushButton("Загрузить"))
        self.run_button.clicked.connect(self._run)

    def refresh(self) -> None:
        rows = self.ctx.read(state.pending_batches)
        self.batch.clear()
        for row in rows:
            label = f"Батч {row['batch_id']}: {row['folder']}, часть {row['part']} — {row['n']} файлов"
            self.batch.addItem(label, row["batch_id"])
        if rows:
            self.batch.setCurrentIndex(self.batch.count() - 1)  # the last pending batch by default
        self.status.setText("" if rows else "Нет файлов, ожидающих загрузки")
        self.run_button.setEnabled(bool(rows) and not self.busy)

    def _run(self) -> None:
        batch = self.batch.currentData()
        if batch is None:
            return
        try:
            settings = self.ctx.prefs.build_settings()
        except (ValidationError, ValueError) as error:
            self.write(f"Проверьте настройки SFTP: {error}")
            return
        if not settings.sftp_user or not settings.sftp_password.get_secret_value():
            self.write(
                "Не заданы логин и пароль SFTP. Откройте «Настройки», введите их и нажмите «Сохранить»; "
                "кнопка «Проверить SFTP» входит на сервер, ничего не загружая."
            )
            self.status.setText("Нужны логин и пароль SFTP")
            return

        def work(conn, progress, should_stop):
            return services.upload_batch(conn, int(batch), settings, progress, should_stop)

        self.start(work, self._done)

    def _done(self, report: object) -> None:
        if not isinstance(report, services.UploadReport):
            return
        if report.error:
            self.write(f"ОШИБКА {report.error}")
            self.write("Файлы остались в очереди. Проверьте SFTP в «Настройках» (кнопка «Проверить SFTP»).")
            self.status.setText("Не удалось загрузить")
        else:
            self.write(f"Загружено: {report.uploaded} из {report.total}, ошибок: {len(report.failed)}")
            if report.stopped:
                self.write("Отменено пользователем.")
            if report.uploaded and not report.failed:
                self.write("Дальше: вкладка «CSV и отправка».")
            self.status.setText("Готово" if report.ok else "Готово, есть ошибки")
        self.refresh()
