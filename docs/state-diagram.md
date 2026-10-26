# Состояния

```mermaid
stateDiagram-v2
    [*] --> collecting
    collecting --> collecting: ответ или исправление
    collecting --> ready: вопросы закончились
    ready --> collecting: ответ меняет ветвь
    ready --> ready: присоединены сведения реестра или оценка
```

Результат `ready` содержит рекомендацию R01/R02/R03, а не банковское решение.

```mermaid
stateDiagram-v2
    [*] --> running
    running --> waiting_for_captcha: ФНС запросила код
    waiting_for_captcha --> running: пользователь ответил или обновил картинку
    running --> completed: результат, в том числе partial
    running --> failed: ошибка выполнения
    running --> cancelled: пользователь остановил
    waiting_for_captcha --> cancelled: пользователь остановил
```

Истечение срока капчи может завершить источник с ошибкой и весь job с частичным результатом. `completed` само по себе не означает, что все источники доступны: проверяйте `result.status` и `result.sources`.
