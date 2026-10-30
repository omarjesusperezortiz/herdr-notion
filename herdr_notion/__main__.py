import sys

if sys.argv[1:2] == ["token"]:
    from .tokencmd import main
else:
    from .tui import main

main()
