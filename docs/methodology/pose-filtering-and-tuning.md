# Фильтрация pose и настройка env

Все переменные читаются через `app/services/camera_config.py` и прокидываются в Docker через `.env` / `docker-compose.yml`.

## Runtime filtering (мост)

| Переменная | Default | Назначение |
|------------|---------|------------|
| `CRAN_POSE_FPS` | 8–10 | Частота цикла pose и Modbus |
| `CRAN_POSE_SMOOTH_ALPHA` | 0.55 | EMA: выше → резче отклик, ниже → плавнее |
| `CRAN_POSE_WINDOW` | 5 | Размер медианного окна; `1` = без медианы |
| `CRAN_POSE_MAX_STEP_M` | 0.022 | Max шаг X между кадрами (gate) |
| `CRAN_POSE_OUTLIER_M` | 0.020 | Отброс оценок среди маркеров одного кадра |
| `CRAN_POSE_HOLD_LAST` | 1 | Удержать последний valid при пропадании маркеров |
| `CRAN_POSE_USE_SUBPIX` | 1 | cornerSubPix для ArUco |
| `CRAN_POSE_USE_SOLVEPNP` | 1 | IPPE_SQUARE перед fallback |
| `CRAN_POSE_SKIP_JPEG` | 1 | BGR напрямую без JPEG (pose pipeline) |
| `CRAN_POSE_DEBUG` | 0 | Подробные логи `[POSE-DEBUG]` |

## Spatial / калибровка

| Переменная | Default | Назначение |
|------------|---------|------------|
| `CRAN_MIN_TRUST_HITS` | 7 | Наблюдений для подтверждения landmark |
| `CRAN_MAX_TRUST_SIGMA_M` | 0.05–0.08 | Max σ кластера при калибровке |
| `CRAN_MIN_LANDMARK_SEPARATION_M` | 0.03 | Min расстояние между landmarks |
| `CRAN_MERGE_TOLERANCE_M` | 0.012–0.02 | Слияние наблюдений в кластер |
| `CRAN_RUNTIME_MATCH_TOLERANCE_M` | 0.03–0.04 | Match detection → landmark в runtime |

## Пресет: плотная сетка (~4–6 см между маркерами)

```bash
CRAN_POSE_FPS=10
CRAN_POSE_OUTLIER_M=0.018
CRAN_POSE_MAX_STEP_M=0.022
CRAN_POSE_WINDOW=3
CRAN_POSE_SMOOTH_ALPHA=0.55
CRAN_MIN_LANDMARK_SEPARATION_M=0.028
CRAN_MERGE_TOLERANCE_M=0.012
CRAN_RUNTIME_MATCH_TOLERANCE_M=0.030
```

## Пресет: редкие маркеры (5–7 м)

```bash
CRAN_POSE_OUTLIER_M=0.06
CRAN_POSE_MAX_STEP_M=0.05
CRAN_POSE_WINDOW=13
CRAN_POSE_SMOOTH_ALPHA=0.42
CRAN_MIN_LANDMARK_SEPARATION_M=2.0
CRAN_MERGE_TOLERANCE_M=0.045
CRAN_RUNTIME_MATCH_TOLERANCE_M=0.12
CRAN_MIN_TRUST_HITS=5
CRAN_MAX_TRUST_SIGMA_M=0.18
```

> После смены пресета spatial **перекалибруйте XY**.

## Правило для `RUNTIME_MATCH_TOLERANCE`

Допуск match должен быть **значительно меньше половины** минимального зазора между соседними landmarks на рельсе:

```
RUNTIME_MATCH ≪ min_spacing / 2
```

## Применение изменений

```bash
# отредактировать .env
docker compose up -d bridge-supervisor hook-supervisor calibration-app
```
