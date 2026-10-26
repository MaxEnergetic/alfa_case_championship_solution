# Модель данных

SQLite (`/data/applications.db`, том `applications-data`). Схема создаётся при старте сервиса `applications` (`store.py`).

```mermaid
erDiagram
    users ||--o{ sessions : "входит"
    users ||--o{ applications : "владеет"
    applications ||--o{ status_history : "история"
    applications ||--o{ consents : "согласия"
    applications ||--o{ documents : "файлы"

    users {
        text email PK
        text name
        text role "client | staff"
        text password_salt
        text password_hash "PBKDF2-SHA256"
    }
    sessions {
        text token_hash PK "SHA-256 токена"
        text email FK
        text expires_at
    }
    applications {
        text id PK
        text display_id "номер для клиента"
        text owner_email FK
        text status
        text data "JSON заявки"
        real next_at "когда системе делать следующий шаг"
        int attempts "повторы при сбоях"
        text created_at
        text updated_at
    }
    status_history {
        int id PK
        text application_id FK
        text from_status
        text to_status
        text actor "Клиент | Система | ФИО сотрудника"
        text label
        text at
    }
    consents {
        int id PK
        text application_id FK
        text type "bki | privacy"
        text text
        text user_email
        text at
    }
    documents {
        text id PK
        text application_id FK
        text filename
        text content_type
        int size
        text sha256
        text path
        text uploaded_at
    }
```

## `applications.data` (JSON)

| Поле | Содержимое | Источник |
|---|---|---|
| `form` | Ответы анкеты: цель, способ получения, сумма, срок, максимальный срок, залог, ИНН, заявитель, выручка, платежи, согласия | Клиент (автосохранение) |
| `wizardStage`, `wizardIndex` | Шаг анкеты, на котором остановился клиент | Клиент |
| `company` | Карточка компании в формате кабинета | ЕГРЮЛ/ЕГРИП + ФНС + ФССП (моки) |
| `product` | Выбранный продукт и ставка | Кредитная логика |
| `applicant` | Заявитель, роль, доверенность | Клиент |
| `enrichment` | Сырые ответы источников, включая БКИ (клиенту не отдаётся) | Моки |
| `decision` | Исход, сумма, срок, ставка, платёж, причина, балл с разбивкой по факторам, скор ML-модели | scoring + decision.py / сотрудник |
| `documentName`, `submittedAt`, `acceptedAt`, `signedAt`, `issuedAt` | Отметки процесса | Сервис |

Почему JSON-документ: анкета и ответы источников меняются чаще схемы; поля, по которым идёт выборка (статус, владелец, сроки), вынесены в колонки.
