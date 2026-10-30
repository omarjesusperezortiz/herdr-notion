import sys

cmd = sys.argv[1:2]
if cmd == ["token"]:
    from .tokencmd import main
elif cmd in (["login"], ["logout"]):
    from .tokencmd import login_main as main
else:
    from .tui import main

main()
