import sys

if sys.argv[1:2] in (["login"], ["logout"]):
    from .tokencmd import login_main as main
else:
    from .tui import main

main()
