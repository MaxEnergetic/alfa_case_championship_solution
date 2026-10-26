# Диаграмма состояний заявки

Статусы хранит сервис `applications` (поле `applications.status`), каждая смена пишется в `status_history`.

```mermaid
stateDiagram-v2
    [*] --> draft: клиент создал черновик
    draft --> submitted: отправка (согласия 218-ФЗ и 152-ФЗ)
    submitted --> checking: система
    checking --> declined: стоп-фактор
    checking --> needs_docs: нет отчётности в ФНС
    checking --> enrichment: данных достаточно
    needs_docs --> enrichment: клиент загрузил PDF-выписку
    enrichment --> scoring: ЕГРЮЛ, ФНС, ФССП, БКИ получены
    scoring --> approved: балл ≥ 50, платёж проходит
    scoring --> counteroffer: балл ≥ 50, платёж не проходит
    scoring --> manual_review: балл 35–49
    scoring --> declined: балл < 35
    manual_review --> approved: сотрудник
    manual_review --> counteroffer: сотрудник
    manual_review --> declined: сотрудник
    approved --> accepted: клиент принял
    counteroffer --> accepted: клиент принял
    approved --> rejected_by_client: клиент отказался / изменил параметры
    counteroffer --> rejected_by_client: клиент отказался / изменил параметры
    accepted --> signed: подписание (имитация)
    signed --> issued: зачисление (имитация)
    declined --> [*]
    rejected_by_client --> [*]
    issued --> [*]
```

| Статус | Название в кабинете | Кто переводит дальше |
|---|---|---|
| `draft` | Черновик | Клиент |
| `submitted` | Подана | Система |
| `checking` | Проверяем данные | Система |
| `needs_docs` | Нужны документы | Клиент (загрузка PDF) |
| `enrichment` | Получаем сведения | Система |
| `scoring` | Оцениваем заявку | Система |
| `manual_review` | Ручная проверка | Сотрудник банка |
| `approved` / `counteroffer` | Одобрено / Контрпредложение | Клиент |
| `declined` | Отказ | — |
| `accepted` | Принято клиентом | Клиент (подписание) |
| `rejected_by_client` | Отклонено клиентом | — («Изменить параметры» создаёт новый черновик) |
| `signed` / `issued` | Договор подписан / Деньги зачислены | Система |

Шаги системы выполняет фоновый обработчик с паузой `STEP_SECONDS` (по умолчанию 2,5 с), чтобы в демо было видно движение статуса. При сбое соседнего сервиса шаг повторяется.
