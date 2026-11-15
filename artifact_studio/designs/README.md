# Design feature ownership

`library.py` and `references.py` retain reusable design context. `authoring.py`
and `transfer.py` create/update portable design sources. `preview.py` and
`thumbnails.py` produce inert previews. `slides.py` and `elements.py` own immutable
proposal sets, guarded human decisions and recovery. The three `*_api.py` modules
bind these services to existing HTTP routes; they do not create an app singleton.

Use imports from this package in new code. The historical root modules replace
their entries in `sys.modules` with these modules, so old and new callers share
the same globals, classes and monkeypatches. They contain no feature behavior.
Retain compatibility until a separately announced API change.

Shipped viewer/style resources come from `artifact_studio.PROJECT_ROOT`.
Collection, database and temporary capture roots continue to come from the
existing explicit stores and adapters. This move changes no authorization,
snapshot, proposal, source-composition or tool contract.
