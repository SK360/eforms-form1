"""Optional inventory server for the ATF eForms helper (design doc: "Inventory server design"). Never imported by the app."""
# The helper's version (src/atf.py): the server is released with it, and both are bumped together (a test in the
# app's suite fails when they differ). `atf-server update` and the Server screen compare it with the newest release.
__version__ = "1.3.0"
