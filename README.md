# RU SRT BOT

Telegram-бот для управления SRT-потоками (входящие/исходящие) через FFmpeg. Поддержка нескольких серверов (Москва, Петербург и др.).

## Возможности

- Создание входящих и исходящих SRT-потоков через Telegram
- Мультисерверная архитектура (SPB + MSK)
- Passphrase-защита потоков
- Автоматическое истечение потоков через 24ч с продлением
- Статистика потоков (разрешение, битрейт, кадры, дропы)
- Система отзывов
- Рассылка пользователям
- Админ-панель
- Страница статуса (status_server.py)

## Быстрая установка (одна команда)

```bash
bash <(curl -sL https://raw.githubusercontent.com/web0worm/ru-srt-bot/main/deploy.sh)
```

После установки отредактируйте конфигурацию:

```bash
nano /opt/srt-bot/.env
systemctl restart srt-bot
```

## Установка из клона

```bash
git clone https://github.com/web0worm/ru-srt-bot.git
cd ru-srt-bot
sudo bash deploy.sh
```

## Конфигурация (.env)

| Переменная | Описание |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather |
| `ADMIN_USER_ID` | Telegram ID администратора |
| `SERVER_PUBLIC_IP` | Публичный IP или домен сервера |
| `INCOMING_PORT_START/END` | Диапазон портов для входящих потоков |
| `OUTGOING_PORT_START/END` | Диапазон портов для исходящих потоков |
| `MAX_INCOMING_STREAMS` | Максимум одновременных входящих потоков |
| `SERVERS_CONFIG` | JSON-конфиг мультисерверности (см. `.env.example`) |

## Мультисерверная настройка

Для работы с несколькими серверами:

1. На основном сервере (где бот) настройте SSH-ключи для доступа к удалённым серверам
2. На удалённых серверах установите `ffmpeg` и скопируйте `scripts/ffmpeg_wrapper.sh`, `scripts/kill_port.sh`
3. Пропишите `SERVERS_CONFIG` в `.env` (см. `.env.example`)

```bash
# Генерация SSH-ключа (на основном сервере)
ssh-keygen -t rsa -b 4096 -f /root/.ssh/id_rsa -N ""
ssh-copy-id root@REMOTE_SERVER_IP
```

## Структура проекта

```
├── app/
│   ├── main.py               # Точка входа
│   ├── config.py              # Загрузка настроек
│   ├── bot/
│   │   ├── handlers.py        # Обработчики Telegram
│   │   ├── keyboards.py       # Клавиатуры
│   │   └── messages.py        # Тексты сообщений
│   └── core/
│       ├── models.py          # Модели данных
│       ├── storage.py         # Хранилище состояния
│       ├── server_config.py   # Конфигурация серверов
│       ├── server_manager.py  # Управление удалёнными серверами
│       ├── ffmpeg_manager.py  # Управление FFmpeg
│       ├── analyzer.py        # Парсинг логов FFmpeg
│       ├── reviews_storage.py # Отзывы
│       └── users_storage.py   # Хранилище пользователей
├── scripts/
│   ├── ffmpeg_wrapper.sh      # Обёртка FFmpeg с автоперезапуском
│   ├── kill_port.sh           # Убийство процессов по порту
│   ├── cleanup_ffmpeg.py      # Очистка зомби-процессов (cron)
│   ├── fetch_avatars.py       # Скачивание аватаров (cron)
│   ├── check_tunnel_reminders.py
│   └── send_tunnel_reminders.py
├── status_server.py           # HTTP-сервер статуса
├── deploy.sh                  # Скрипт развёртывания
├── requirements.txt
├── .env.example
└── README.md
```

## Управление

```bash
# Статус бота
systemctl status srt-bot

# Логи в реальном времени
journalctl -u srt-bot -f

# Перезапуск
systemctl restart srt-bot

# Остановка
systemctl stop srt-bot
```

## Требования

- Ubuntu 22.04 / 24.04
- Python 3.10+
- FFmpeg с поддержкой SRT
- Открытые порты для SRT (по умолчанию 4000-7100)
