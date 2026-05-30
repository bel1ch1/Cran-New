# Методологии CRAN

Документация по алгоритмам и протоколам системы калибровки и pose-трекинга крана.

| Документ | Содержание |
|----------|------------|
| [system-architecture.md](system-architecture.md) | Контуры системы, Docker-сервисы, обмен данными |
| [xy-spatial-calibration.md](xy-spatial-calibration.md) | XY-калибровка моста, карта landmarks, система доверия |
| [bridge-pose-runtime.md](bridge-pose-runtime.md) | Runtime-позиция камеры моста относительно маркеров |
| [hook-pose-runtime.md](hook-pose-runtime.md) | Runtime-позиция крюка (Z, отклонение от центра) |
| [modbus-protocol.md](modbus-protocol.md) | Карта регистров Modbus TCP, кодирование float32 |
| [pose-filtering-and-tuning.md](pose-filtering-and-tuning.md) | Фильтрация pose, env-настройки, пресеты под шаг маркеров |

Связанные материалы:

- [Развёртывание системы](../deployment.md)
- [README проекта](../../README.md)
