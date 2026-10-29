# B1.2.8 tester shadow page

Password-protected `/sandbox-test/` serves an isolated static test snapshot through its own Worker. It binds only static assets, with no database, account service, or production save access. Every response is noindexed. Browser storage is namespaced separately from the live game.

`preview-manifest.json` records the application commit and exact public file hashes. The deployment asset directory is excluded from this repository. The protected corresponding-source archive is transported in two pieces and streamed back byte-for-byte at its original download path.

Update from a prepared verified export with `wrangler deploy --config workers/sandbox-b128-preview/wrangler.json`. Disable with `wrangler delete --config workers/sandbox-b128-preview/wrangler.json --force`. This affects only the tester Worker and its two routes. Tester credentials are held separately and must not be added here.
