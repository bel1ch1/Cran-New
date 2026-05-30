# Runtime: позиция крюка (Z)

## Задача

По одному **целевому** ArUco-маркеру (`hook_calibration.marker_id`) вычислить:

| Выход | Modbus | Смысл |
|-------|--------|--------|
| `distance_m` | float32 | Скорректированная дистанция до маркера, м |
| `deviation_x_px` | float32 | Смещение центра маркера от центра кадра по X, px |
| `deviation_y_px` | float32 | Смещение по Y, px |
| `marker_id` | uint16 | ID из конфига |
| `valid` | 0/1 | Маркер найден в кадре |

## Алгоритм

Реализация: `hook_pose_modbus.py` → `compute_hook_pose`.

1. Полный кадр → grayscale → `detectMarkers`.
2. Если `marker_id` не среди детекций → `valid=0`.
3. `estimatePoseSingleMarkers` → tvec `(x, y, z)`.
4. **Дистанция** с учётом бокового смещения:

   ```
   lateral = sqrt(x² + y²)
   angle = atan2(lateral, z)
   distance = z / cos(angle)
   ```

5. **Отклонение в пикселях** — центр углов маркера минус центр кадра.

## Modbus

Hook **не поднимает** сервер. Подключается к `bridge-supervisor:5020` и пишет регистры **200+** (по умолчанию) через `write_registers`.

См. [modbus-protocol.md](modbus-protocol.md).

## Калибровка

1. `/z-settings` — размер маркера (мм) и **ID** маркера на крюке.
2. `/z-calib` — проверка детекции в кадре.
3. Сохранение в `hook_calibration` секцию JSON.

## Замечания

- Hook runtime пока **не использует** subpix/solvePnP из bridge pipeline — только `estimatePoseSingleMarkers`.
- Частота кадров: `CRAN_POSE_FPS` (как у bridge) через `resolve_pose_fps()`.
