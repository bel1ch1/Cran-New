def get_command_message(command: str) -> str:
    command_map = {
        "start_z_regular": "Запуск калибровки крюка выполнен",
        "stop_z_regular": "Остановка калибровки крюка выполнена",
        "start_xy_regular": "Запуск калибровки моста выполнен",
        "stop_xy_regular": "Остановка калибровки моста выполнена",
        "restart": "Команда перезагрузки системы отправлена",
    }
    return command_map.get(command, f"Команда {command} обработана")

