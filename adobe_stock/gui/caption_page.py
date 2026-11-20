"""Step 2: titles, keywords and categories from Claude."""

from PySide6.QtWidgets import QComboBox, QFormLayout, QMessageBox, QPushButton, QSpinBox

from adobe_stock import caption, services
from adobe_stock.gui.context import AppContext
from adobe_stock.gui.task_page import TaskPage


class CaptionPage(TaskPage):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            ctx,
            "Подписи (Claude)",
            "Claude смотрит уменьшенные копии (оригиналы не меняются) и пишет заголовок, ключевые слова и "
            "категорию. Нужен установленный и авторизованный Claude Code; API-ключ не нужен. Результат "
            "сохраняется в базу.",
        )
        form = QFormLayout()
        self.model = QComboBox()
        for model_id, note in caption.MODELS:
            self.model.addItem(f"{model_id} — {note}", model_id)
        self.model.setCurrentIndex(max(self.model.findData(ctx.prefs.model), 0))
        form.addRow("Модель", self.model)
        self.limit = QSpinBox()
        self.limit.setRange(1, 5000)
        self.limit.setValue(services.DEFAULT_CAPTION_LIMIT)
        form.addRow("Файлов за запуск", self.limit)
        self.per_call = QSpinBox()
        self.per_call.setRange(1, 10)
        self.per_call.setValue(services.DEFAULT_IMAGES_PER_CALL)
        form.addRow("Картинок в одном вызове", self.per_call)
        self.workers = QSpinBox()
        self.workers.setRange(1, 4)
        form.addRow("Параллельных вызовов", self.workers)
        self.form.addLayout(form)
        self.run_button = self.add_action(QPushButton("Подписать"))
        self.run_button.clicked.connect(self._run)

    def _run(self) -> None:
        if caption.find_claude() is None:
            QMessageBox.warning(
                self,
                "Claude Code не найден",
                "Установите Claude Code и войдите в аккаунт, либо укажите путь к `claude` на вкладке "
                "«Настройки» и нажмите «Проверить Claude».",
            )
            return
        model = str(self.model.currentData())
        self.ctx.prefs.set_text("model", model)
        batch_size = self.ctx.prefs.batch_size
        limit, per_call, workers = self.limit.value(), self.per_call.value(), self.workers.value()

        def work(conn, progress, should_stop):
            return services.caption_files(
                conn,
                model,
                limit=limit,
                per_call=per_call,
                workers=workers,
                batch_size=batch_size,
                on_progress=progress,
                should_stop=should_stop,
            )

        self.start(work, self._done)

    def _done(self, report: object) -> None:
        if not isinstance(report, services.CaptionReport):
            return
        if report.total == 0:
            self.write("Нечего подписывать: нет файлов, ожидающих метаданные.")
            self.status.setText("Нечего подписывать")
            return
        self.write(
            f"Подписано: {report.captioned} из {report.total}\nНа проверке: {len(report.flagged)}\n"
            f"Новых батчей: {report.new_batches}" + ("\nОтменено пользователем" if report.stopped else "")
        )
        for message in report.flagged:
            self.write(f"ПРОВЕРКА {message}")
        if report.flagged:
            self.write("Эти файлы не пойдут в загрузку, пока вы не одобрите их на вкладке «Проверка».")
        for message in report.errors:
            self.write(f"ОШИБКА {message}")
        self.status.setText("Готово" if not report.errors else "Готово, есть ошибки")
