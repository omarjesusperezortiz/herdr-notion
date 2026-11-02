import sys

if sys.argv[1:2] in (["login"], ["logout"]):
    from .tokencmd import login_main as main
elif sys.argv[1:2] == ["digest"]:
    from .digest import main as digest_main

    def main():
        sys.exit(digest_main(sys.argv[2:]))
else:
    from .tui import main

main()
