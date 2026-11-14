import argparse
import os
import time
from . import create_app
from .database import connect
from .service import process_next
from .seed import seed


def main():
    parser = argparse.ArgumentParser(description="HookWatch local server and worker")
    parser.add_argument("command", choices=["serve", "seed", "worker"])
    parser.add_argument(
        "--once", action="store_true", help="Process at most one due event"
    )
    args = parser.parse_args()
    app = create_app()
    if args.command == "serve":
        app.run(
            host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "5000")),
            debug=False,
        )
        return
    db = connect(app.config["DATABASE"])
    try:
        if args.command == "seed":
            if not app.config["DEMO_MODE"]:
                raise RuntimeError("Set DEMO_MODE=1 to insert fictional sample data.")
            print("Seeded demo events." if seed(db) else "Existing events preserved.")
        else:
            while True:
                processed = process_next(db)
                if args.once:
                    print("Processed one event." if processed else "No due events.")
                    break
                if not processed:
                    time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        db.close()


if __name__ == "__main__":
    main()
