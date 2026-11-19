# Fleet application

A self-hosted fleet management application with phone/password sign-in, role-based access, trips, document uploads and CSV reports.

## Development

Requirements: Docker Engine or Docker Desktop with Docker Compose v2; Python 3 for configuration generation.

```sh
python3 scripts/init_env.py --local
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
```

Open http://localhost:8000.

```sh
docker compose exec web python manage.py test --verbosity 2
```

The `--local` configuration is for localhost testing. Company-server configuration and operational instructions are distributed separately. Do not commit credentials, real contact lists, uploaded documents or database files.

**Existing deployments:** The migration history in this version differs from the earlier GitHub release `1bcfe45`. Do not run these migrations against that release's existing database. Back up the database and uploads and plan a separate, tested migration or restore before upgrading an existing installation.

`examples/roster.csv` contains synthetic sample data only. The real initial roster is provided separately.

## Stack

Python 3.12, Django 5.2, PostgreSQL 17, Gunicorn and Docker Compose. The GitHub Actions workflow runs application tests and container checks.

## Shared application style

All pages use the same working theme through `templates/base.html` and
`static/fleet.css`: Play typography, gray-green backgrounds, clear off-white
cards and tables, and warm peach action buttons. This includes the fleet,
reports, accounts, trip details, driver screens, forms and sign-in. Do not add
route-specific competing themes, promotional banners, slogans or decorative
logos. The legacy `static/brand.css` is not loaded by the application.
Keep the driver interface focused on assigned trips and documents.
`templates/fleet/workspace.html` is only a compatibility alias for the common base.

## Driver handover history

Vehicle changes are recorded as assignment periods with the effective handover
time, reason, operator and entry time. Existing assignments are seeded with an
unknown start date rather than a fabricated historical date. The edit form and
roster import both record changes through `fleet/assignment_history.py` inside
a transaction. Closed periods and saved tractor/trailer/name snapshots remain
available to logistics at `/vehicles/<id>/history/`. Overlapping backdated
assignments are rejected. To move a driver between vehicles, end the old vehicle
assignment and start the new one at the same handover time.

Permanent vehicle assignment does not rewrite trip drivers or documents. A
substitution within an active trip uses the existing trip handover action and
its audit trail; document files and upload attribution are retained.

## Repairs and upcoming trips

The vehicle menu opens a repair form requiring a reason. Each repair keeps its
effective start/end dates, entry timestamps, operators and completion notes in
the vehicle history. Existing repair flags are seeded with unknown dates and
an explicit note that the original reason was not recorded.

Trips with status `queued` do not occupy the current-trip slot. Dispatchers
choose "У чергу" in the trip form; the vehicle card and assigned driver's
dashboard show upcoming trips sorted by planned date, then creation order.
The driver's open dashboard refreshes every minute while visible. This is an
in-app queue, without external messages or push notifications.

Starting a queued trip is an explicit POST action, permitted to logistics or
its assigned driver only. It locks the vehicle and driver, checks that the
vehicle is not in repair, both are free and this is the first queued trip for
the vehicle, then changes it to `planned`. Database constraints continue to
prevent two simultaneous current trips for either a vehicle or driver.

### Mobile driver delivery
Drivers can add up to 10 JPG/PNG/WebP/PDF files per submission (20 MB per file,
40 MB total). Each file is validated before any are saved; a failed batch rolls
back database rows and removes newly written files. Mobile requests use durable
receipts so resending the same request does not create duplicate documents.

Driver acceptance, queue start and trip stage actions are confirmed by the server
before the page advances. One pending action is retained in local storage per
signed-in driver and retried while the page is open, on reconnect, or on the next
visit. The receipt UUID and original trip revision prevent duplicate execution
and stale changes. A device being online is never treated as server confirmation.
This does not provide background delivery while the browser is closed, or cache
private pages/documents offline. Photos remain in the current open form and must
be submitted again after reconnect if no confirmation was received.

Driver map links open Google Maps directions to each address from the device's
current location, when available. These are ordinary map directions.

Additional frontend fault tests: `node --test tests/driver_sync.test.cjs`.

### Trip transfers
Dispatcher reassignment moves the trip to the new driver's current vehicle, or
permanent vehicle if there is no active trip. Without either, the existing trip
vehicle is retained. Permanent vehicle assignments are not changed. Busy drivers,
busy target vehicles, and vehicles under repair receive the transferred trip in
the queue, with priority as the next trip. The receiving driver must acknowledge
the transferred task. A transfer receipt links the previous driver and vehicle,
new driver and vehicle, previous/new stage, actor, and time; documents stay on the
same trip and remain discoverable in the previous vehicle's history.

### Driver substitution on the same vehicle
Fleet card menu → Driver substitution opens a separate handover form. The trip
keeps its vehicle, stage, cargo, weights and documents. Only a free driver can
receive an active handover; busy drivers are excluded and rechecked under locks.
Optional effective time, location and note are stored with the previous/new
driver, stage and actor. Trip and vehicle history distinguish substitution from
a transfer to another vehicle. The new driver acknowledges the handover before
continuing; permanent vehicle assignment is unchanged.

### Verified route points
Each stop may store a latitude/longitude pair. Dispatchers choose and confirm a
pin in the trip form or the address pencil editor. Drivers get coordinate-based
Google Maps directions; stops without coordinates open address search instead.
Editing an address requires confirming an unchanged pin again, or clearing it.
Report address corrections clear the old pin and record that in the audit log.

Leaflet 1.9.4 is bundled locally under `static/vendor/leaflet` with its license.
Map tiles load only when a map is opened. `MAP_TILE_URL` and
`MAP_TILE_ATTRIBUTION` configure the provider; CSP permits images from that
configured HTTPS origin while keeping scripts and connections same-origin.
`MAP_GEOCODER_URL` configures the server-side search endpoint. The default
Nominatim service is used only for explicit search clicks, with seven-day
database caching, application identification, and a database lock limiting the
whole application to at most one upstream request per second. No autocomplete,
background geocoding or tile prefetch is used. Keep the OSM attribution visible
and review https://operations.osmfoundation.org/policies/nominatim/ and
https://operations.osmfoundation.org/policies/tiles/ before changing providers.


### Рейси з кількома точками

У формі нового рейсу додаються завантаження та розвантаження (до 30 чинних точок). Кожна точка має адресу, координати в’їзду, контакти, вантаж, планову й фактичну вагу, номери ТТН та вказівки. План перевіряється за кожним вантажем; маршрут починається завантаженням і закінчується розвантаженням. Виконані й розпочаті точки не можна переставляти або видаляти. Скасовані зупинки зберігаються в історії.

Водій приймає рейс один раз і послідовно підтверджує прибуття та виконання кожної точки. Завершення проміжного розвантаження залишає рейс активним. Дії використовують ті самі серверні підтвердження, контроль версії та захист від повторів при втраті зв’язку. Зміни маршруту показуються водію до підтвердження ознайомлення; підміна водія зберігає поточну точку.

Підписані ТТН прив’язуються до розвантаження та номера. Для кількох номерів (через крапку з комою) підписана ТТН потрібна для кожного; кілька сторінок одного номера можна надіслати разом. Зеленим рейс стає після отримання всіх потрібних підписаних ТТН. Оригінали відмічаються окремо в кожній точці розвантаження.

Підсумкова фактична вага рейсу — сума фактичних ваг усіх чинних розвантажень; якщо не всі ваги внесено, підсумок залишається невідомим. Завантаження до підсумкового тоннажу повторно не додаються. CSV за замовчуванням містить один рядок на рейс; окреме вивантаження «CSV із точками маршруту» містить операції. Питання щодо документів у «Потребує уваги» та їхні пропуски прив’язуються до конкретної виконаної точки. Фактична вага необов’язкова й не створює питань уваги. Точка на карті теж необов’язкова; водій бачить карту й навігацію лише для явно вибраних координат.

Міграція 0016 переносить наявні рейси у дві точки, зберігаючи зв’язки документів, вагу, прийняття водієм та відомі часові дані. Невідомий час давнього завантаження залишається невідомим. Перед застосуванням потрібно зберегти резервну копію бази.


## Пробіг, пальне, місця й ТО

«Звіти → Пробіг і пальне» показує завершені рейси за період, підсумки за авто та водіями, пробіг за днями й CSV. Середня витрата — сума літрів / сума кілометрів × 100 лише за рейсами з обома показниками та додатним пробігом. Порожні поля не стають нулями. Дані можна уточнити у звіті або в рейсі. При підміні водія весь рейс відноситься до водія на момент завершення; автоматичного розподілу кілометрів між водіями немає.

«Збережені місця» — довідник адрес, областей, районів, громад, контактів і підтверджених заїздів. Вибір місця копіює дані в точку рейсу. Зміна чи архівування довідника не змінює старі рейси. Нові адреси додаються через вкладку «Збережені місця»; у формі рейсу можна обрати готове місце.

Фото документів відкриваються в переглядачі з мініатюрами, збільшенням і перемиканням. PDF використовує вбудований переглядач браузера з посиланням для окремого відкриття. Оригінали залишаються доступними для завантаження. Обидва способи перевіряють поточний доступ до рейсу; файли не публікуються як загальнодоступні media URL.

У водія ненадіслані файли зберігаються у IndexedDB цього браузера з прив’язкою до користувача, рейсу й точки. На екрані є повідомлення про збереження або помилку пам’яті; зверху — посилання на локальні чернетки. Видалення локальної пачки відбувається після підтвердження сервера. Невизначений результат надсилання зберігає UUID та незмінну пачку для повторної спроби без дублювання. Конфлікт між вкладками блокує перезапис застарілою чернеткою. Надсилання після відновлення зв’язку запускає сам водій; закрита сторінка не виконує фонових завантажень. Очищення даних браузера, приватний режим або видалення сховища операційною системою може прибрати локальні чернетки — це не резервна копія галереї телефону.

«ТО й документи авто» містить терміни за датою/одометром, пороги нагадування, розділення тягач/причіп, історію виконання й необов’язкове повторення. Одометр логіст вносить окремо; кілометри рейсів автоматично до нього не додаються. Запис виконання за минулу дату не перемотує поточний одометр. Початкові номери авто зберігаються в записі. Міграція 0020 додає довідник, плани та nullable-поля одометра без зміни наявних рейсів.
