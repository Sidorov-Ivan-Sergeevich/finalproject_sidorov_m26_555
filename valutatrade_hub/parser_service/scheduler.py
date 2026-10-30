"""Run the parser once or periodically as a separate console process."""

import argparse
import time

from valutatrade_hub.core.exceptions import ValutaTradeError
from valutatrade_hub.infra.database import DatabaseManager
from valutatrade_hub.infra.settings import SettingsLoader
from valutatrade_hub.logging_config import configure_logging
from valutatrade_hub.parser_service.updater import build_updater


def main() -> int:
    """Run selected providers once or at a configurable interval."""
    parser = argparse.ArgumentParser(description="ValutaTrade rates parser")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=900)
    parser.add_argument(
        "--source",
        choices=["coingecko", "exchangerate"],
    )
    arguments = parser.parse_args()

    if arguments.interval < 30:
        parser.error("--interval должен быть не меньше 30 секунд")

    try:
        settings = SettingsLoader()
        configure_logging(settings)
        database = DatabaseManager(settings)
        database.initialize()
        updater = build_updater(settings, database)

        while True:
            try:
                result = updater.run_update(arguments.source)
                print(result)
            except ValutaTradeError as error:
                print(error)
                if arguments.once:
                    return 1

            if arguments.once:
                return 0

            time.sleep(arguments.interval)

    except KeyboardInterrupt:
        print("\nПарсер остановлен.")
        return 0
    except (ValutaTradeError, ValueError, OSError) as error:
        print(f"Не удалось запустить парсер: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
