# Wingman state snapshots

`wingman state save` writes the compressed SQLite snapshot and checksum metadata
into this directory. Those generated files are intentionally tracked through
Git so another device can restore the same inbox state.

The snapshot contains creator workflow data, including viewer usernames and
comments, generated and edited replies, classifications, ignored state, and
video-specific context. It does not contain `.env`, API credentials, Google
OAuth tokens, or the creator profile.

Only publish snapshots to a private repository whose collaborators are allowed
to access this data. The snapshot is compressed but not encrypted.
