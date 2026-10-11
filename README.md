# Smart Parking

Учебная система управления парковкой: клиентский сайт без регистрации и защищённая панель администратора. Стек — Python 3.10+, Flask, SQLite, Jinja2, HTML, CSS и JavaScript. Внешние сервисы, SMS и онлайн-платежи не используются.

## Возможности

- Выбор свободного места, ввод ФИО, телефона и номера автомобиля.
- Личная ссылка на парковку, таймер, стоимость и сообщения администратора.
- Клиентский и административный выезд с сохранением истории.
- Специальные места: бесплатная парковка после подтверждения права администратором.
- Администратор: поиск автомобилей, сведения клиента, сообщения, история, удаление завершённых записей, настройка тарифов и парковок.

Первый час бесплатный, затем 100 ₸ за каждый начатый дополнительный час. Тариф можно изменить; он фиксируется для сессии при въезде. Стоимость вычисляется сервером. Отмена активной парковки не освобождает место и не позволяет обходить начисления.

## Структура

```text
Smart-Parking/
  app.py                 # Flask, запуск, совместимость старых URL
  database.py            # SQLite и сохранённая схема исходной базы
  smart_parking.py       # Импорт старых бронирований и общие функции
  parking_portal.py      # Действующие страницы, API и проверки
  manage.py              # Настройка администратора
  requirements.txt
  README.md
  LICENSE
  .env.example
  .gitignore
  templates/             # 5 действующих шаблонов
  static/
    css/parking-control.css
    parking-icons.svg
    portal-common.js
    client-parking.js
    admin-parking.js
    admin-login-portal.js
  tests/                 # Python и JavaScript проверки
  parking_app.db
  *.bak                  # Сохранённые резервные копии базы
  .cleanup-backups/      # Архив и отчёт о согласованной очистке
  .git/
```

Исходные таблицы `parking_lots`, `parking_slots`, `parking_bookings`, `users` и `admin` сохранены для совместимости и происхождения данных. Не удаляйте их вручную. Автомобили и сессии хранятся в `vehicles` и `parking_sessions`; остальные служебные таблицы обеспечивают авторизацию, тарифы, сообщения и защиту удаления. Старые страницы заменены совместимыми перенаправлениями; старые операции записи возвращают 410.

## Установка и запуск

В PowerShell из папки проекта:

```powershell
Set-Location 'C:\Users\User\Smart-Parking'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Если доступна команда `py`, используйте `py -3 -m venv .venv`. SQLite входит в Python, отдельный сервер базы не нужен. Клиентский сайт: http://127.0.0.1:5000, панель: http://127.0.0.1:5000/admin.

На этой машине доступен Python с Flask из комплекта pgAdmin. Для локального запуска без нового окружения:

```powershell
$parkingPython = 'C:\Program Files\PostgreSQL\18\pgAdmin 4\python\python.exe'
& $parkingPython -B -c "import sys,runpy;sys.path.insert(0,'.');runpy.run_path('app.py',run_name='__main__')"
```

Запускайте один экземпляр приложения на порту 5000. Для переносимого окружения предпочтительнее отдельный Python и `.venv`.

## Администратор

Существующие учётные записи сохраняются. Если администратора ещё нет:

```powershell
.\.venv\Scripts\python.exe manage.py create-admin --username ВАШ_ЛОГИН
```

Команда скрыто запрашивает ваш пароль и повтор. Допустимо 12–256 символов; логин — 3–80 букв, цифр, точек, дефисов или подчёркиваний. Пароля по умолчанию нет. При повторном запуске с существующим логином пароль меняется, прежние сеансы входа отзываются.

Эквивалент для Python pgAdmin, с вводом собственного логина:

```powershell
$adminUsername = Read-Host 'Введите логин администратора'
& $parkingPython -B -c "import sys,runpy;sys.path.insert(0,'.');username=sys.argv[1];sys.argv=['manage.py','create-admin','--username',username];runpy.run_path('manage.py',run_name='__main__')" $adminUsername
```

Введите выбранные данные на `/admin`. Пароль хранится как хеш scrypt; сеанс входа проверяется на сервере и действует 8 часов.

## Конфигурация

| Переменная | Назначение |
| --- | --- |
| `PARKING_DATABASE` | Путь SQLite; по умолчанию `parking_app.db` рядом с кодом |
| `SECRET_KEY` | Стабильный длинный случайный ключ Flask для постоянного запуска |
| `HOST`, `PORT` | По умолчанию `127.0.0.1`, `5000` |
| `FLASK_DEBUG` | `1` для отладки; по умолчанию выключена |
| `COOKIE_SECURE` | `1` при HTTPS; для локального HTTP оставьте `0` |

Пример в `.env.example`. `.env` автоматически не загружается: задавайте переменные через окружение. Если `SECRET_KEY` не задан, приложение создаёт ключ в памяти на запуск; после перезапуска обновите страницы, чтобы получить новую CSRF-сессию. Для доступа из интернета отдельно настройте HTTPS и сервер развёртывания.

## Основные API

| Метод | Адрес | Назначение |
| --- | --- | --- |
| GET | `/api/spots` | Публичная схема, статистика и тариф без личных данных |
| GET | `/api/spots/available` | Свободные места |
| POST | `/api/parking/enter` | Въезд: full_name, phone, license_plate, spot_id, accessibility_request |
| GET | `/api/parking/my-session` | Личная сессия |
| GET | `/api/parking/my-messages` | Личные сообщения |
| POST | `/api/parking/my-session/exit` | Клиентский выезд |
| POST | `/api/parking/my-session/cancel` | Отказ отмены активной парковки |
| POST | `/api/admin/login`, `/api/admin/logout` | Вход и выход |
| GET | `/api/admin/sessions` | Сессии; поиск q и фильтр status |
| POST | `/api/admin/sessions/<id>/exit` | Выезд администратора |
| DELETE | `/api/admin/sessions/<id>` | Удаление завершённой записи; confirm: true |
| GET | `/api/admin/sessions/<id>/messages` | История сообщений |
| POST | `/api/admin/sessions/<id>/message` | Сообщение: body |
| POST | `/api/admin/sessions/<id>/verify-accessibility` | Проверка права: confirmed |
| POST | `/api/admin/tariff` | Тариф: free_minutes, hourly_fee |
| POST | `/api/admin/spots/<id>` | Тип места: is_accessible |
| POST | `/api/admin/lots` | Парковка: name, address, total_spots, accessible_spots |

Личные API требуют `Authorization: Bearer <token>` из личной ссылки. В SQLite хранится только хеш токена. Административные API требуют серверного сеанса входа. POST/DELETE защищены CSRF: cookie и `X-CSRF-Token` из метатега страницы. Изменения выполняются в транзакциях; дубли активных сессий запрещены индексами.

## История и резервные копии

Новые даты хранятся в UTC, отображаются по Asia/Qyzylorda. Старые даты без часового пояса интерпретируются как +05:00. Неверная хронология показывается как «Некорректные данные»; импортированные или подозрительно старые активные записи требуют проверки и не создают огромные начисления. Их даты, номера и сохранённые суммы не исправляются автоматически.

Удалить можно только завершённую запись после отдельного подтверждения. Перед изменением схемы и удалением создаётся резервная копия; при ошибке копирования удаление не выполняется. Удаляются только выбранная сессия и её сообщения. Автомобили, остальные сессии и занятость мест не меняются. Служебная отметка предотвращает повторный импорт и повторное использование идентификатора удалённой сессии.

Сохраняйте базу, все `.bak` и `.cleanup-backups`. Они исключены из Git, как и секретная конфигурация. Очистка файлов приложения не удаляет пользовательские данные и не меняет схему базы.

## Проверки

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
node --test tests/admin-history.test.cjs tests/client-timer.test.cjs tests/client-booking.test.cjs tests/parking-map.test.cjs
node --check static/portal-common.js
node --check static/client-parking.js
node --check static/admin-parking.js
node --check static/admin-login-portal.js
```

Для найденного Python pgAdmin первая команда: `& $parkingPython -B -m unittest discover -s tests -v`.

Тесты используют временную SQLite и проверяют регистрацию, вход, поиск, цены, выезд, доступность, сообщения, историю, удаление, конфликты, безопасность и совместимость старых URL. JavaScript-проверки используют минимальное окружение DOM; это не визуальные тесты настоящего браузера. Внешний вид и работу на мобильном устройстве следует проверить вручную, если подключённый браузер недоступен.

Проект сохраняет исходную лицензию MIT — `LICENSE`.
