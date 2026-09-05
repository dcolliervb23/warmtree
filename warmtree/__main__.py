"""Allow `python -m warmtree`, which the background refill uses."""

from warmtree.cli import main

raise SystemExit(main())
