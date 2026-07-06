# Media Workflow — from artist file to venue screen

*Analysis of the current workflow + improvement options. Decision needed only
if we want option C (needs IT involvement).*

## Current workflow (works, stays supported)

```
Artist → PM uploads to ARS SharePoint → Tech downloads to laptop
      → Tech uploads via dashboard → assigns to devices → Pi syncs
```

Honest assessment: for a 5-day festival with a bounded set of works this is
**fine**. The v0.3 dashboard removes most of the friction the old webmask had:

- **multi-file drag & drop** with per-file progress (no more one-at-a-time),
- assignment **per device or whole venue group in one tap** (no manual
  manifest publishing anymore),
- works from a phone, so the upload can happen from the venue,
- and for last-minute/offline situations the **USB stick** and **SD card**
  paths bypass the server entirely.

The remaining manual step — download from SharePoint, re-upload to the fleet
server — costs a few minutes per file and keeps a human QA moment in the loop
(check codec/resolution/filename before it goes live). That is not purely a
bug; wrong-format files caught here never brick a venue screen.

## Improvement options

### A) Keep it manual, add conventions (zero effort, recommended baseline)
- One SharePoint folder per venue, agreed filename scheme
  (`<venue>_<artwork>_<v2>.mp4`).
- PMs mark files "final" (SharePoint label or a `_FINAL` suffix) so tech
  never ships drafts.
- Tech uploads via dashboard as today.

### B) Server-side "fetch from link" (small feature, no IT needed)
Paste a SharePoint/OneDrive **share link** into the dashboard; the server
downloads the file itself (no laptop round-trip, big files don't ride on
venue Wi-Fi twice). Caveat: works only if the org allows
"anyone with the link" sharing, which many tenants disable — needs a
30-second test with a real ARS share link. If links require login, this
becomes option C.

### C) Watched SharePoint inbox via Microsoft Graph (the real upgrade)
The server syncs a designated SharePoint folder (e.g. `AEF26-Media/inbox/<venue>/`)
every few minutes (rclone or Graph API). New files appear in the dashboard
as "inbox" items, one click to accept + assign.
- PM workflow becomes: *drop file in SharePoint → done.*
- Needs from ARS IT: an **app registration / service account** with
  read-only access to that one document library. That's the whole ask, but
  it is an ask — lead time realistically weeks, and festival IT is busy.

### Recommendation
Ship **A** now (it's already done in v0.3). Test **B** with one real share
link — if it works, it's an afternoon of code. Raise **C** with IT only if
media volume this year clearly outgrows the manual step; for ~20 venues of
looped content it's probably over-engineering.

## Related: the offline paths

Both bypass SharePoint entirely and are first-class, not hacks:
- **USB stick** into a running Pi → plays + pins within seconds.
- **SD FLEET-MEDIA partition** → load media on any laptop, boot, plays.

For venues with unreliable Wi-Fi these are the *primary* path, with the
server as backup — the pin model keeps both in sync with the dashboard view.
