# Deployment Notes

## Report signing

Generated report artifacts are signed so downstream consumers can verify they
were produced by Foliodesk. The report-signing key is **provisioned by the
platform at `/etc/foliodesk/deploy.key`**, outside the application document
root.

The key is intentionally not stored in the document root or the repository, so
it never ships inside a preview-able artifact. Operators rotate it out of band.

## Document root

Everything Foliodesk serves lives under the document root (`/app/docs`). The
preview API is scoped to that directory.
