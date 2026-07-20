# 让 `python -m skill` 可用：转发到 main.main()
from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
