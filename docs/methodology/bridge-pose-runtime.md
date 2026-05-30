# Runtime: позиция камеры моста (XY)

## Задача

На каждом кадре (~`CRAN_POSE_FPS` Гц) вычислить:

| Выход | Modbus | Смысл |
|-------|--------|--------|
| `camera_x_m` | X | Позиция камеры вдоль рельсы, м |
| `distance_m` | Y | Расстояние до маркера у центра кадра, м |
| `marker_id` | uint16 | ArUco ID этого маркера |
| `valid` | 0/1 | Удалось ли вычислить pose |

## Пайплайн

Реализация: `app/services/bridge_pose_estimator.py`, entrypoint: `bridge_pose_modbus.py`.

1. **ROI** — обрезка кадра из конфига.
2. **ArUco** — `detectMarkers`, опционально `cornerSubPix`.
3. **PnP** — `solvePnP (IPPE_SQUARE)` или `estimatePoseSingleMarkers` → `rel_x_m`, `distance_m`.
4. **Spatial match** — для каждого маркера оценка `camera_x` через карту landmarks (см. ниже).
5. **Fusion** — outlier filter, взвешенное среднее, gate, медианное окно, EMA.
6. **Hold last** — при пропадании маркеров возвращается последний valid pose (`CRAN_POSE_HOLD_LAST`).
7. **Modbus** — запись в holding-регистры через `write_bridge_pose_to_modbus_store`.

## Spatial matching (не по ArUco ID)

Ключи `marker_positions_m` — **слоты по X**, не ID маркеров.

Для каждого наблюдения:

```
camera_x = landmark_x − axis_sign × rel_x_m
```

**Опорный маркер** (`reference_marker_id`):

```
camera_x = zero_marker_offset_m − axis_sign × rel_x_m
```

Для остальных маркеров нужен **hint** (приблизительный camera_x):

- из опорного маркера в кадре;
- из прошлого кадра (`last_camera_x_m`);
- **bootstrap** — перебор пар «наблюдение × landmark» и медиана кандidатов.

Match landmark: вычислить `abs_x = hint + axis × rel_x`, найти ближайший landmark в допуске `CRAN_RUNTIME_MATCH_TOLERANCE_M`.

## Веса маркеров

```
weight = 1 / (0.05 + distance_m²)
```

Ближние маркеры доминируют в fusion.

## Фильтрация (кратко)

| Этап | Env | Назначение |
|------|-----|------------|
| Outlier | `CRAN_POSE_OUTLIER_M` | Отброс оценок вне медианы среди маркеров кадра |
| Gate | `CRAN_POSE_MAX_STEP_M` | Отброс скачка между кадрами |
| Window | `CRAN_POSE_WINDOW` | Медиана по окну (после заполнения буфера) |
| EMA | `CRAN_POSE_SMOOTH_ALPHA` | Сглаживание X и Y |
| Hold | `CRAN_POSE_HOLD_LAST` | Удержание последнего valid |

Подробные пресеты: [pose-filtering-and-tuning.md](pose-filtering-and-tuning.md).

## Отладка

```bash
CRAN_POSE_DEBUG=1
docker compose up -d bridge-supervisor
docker compose logs -f bridge-supervisor | grep POSE
```

Логи: `[POSE] marker=… X=… Y=…`, `[POSE-DEBUG] … spatial match=0`.
