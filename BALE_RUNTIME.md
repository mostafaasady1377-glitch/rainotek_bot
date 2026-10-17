# Shared Telegram / Bale runtime

Run `python main_dual.py` instead of `python main.py`. Never run both entry points concurrently: that duplicates Telegram polling and inventory reconciliation.

If the configured proxy is unavailable but direct Telegram/Bale API connectivity has been verified, use `python main_dual.py --direct-network`. This bypasses the proxy for this process only, including Sheets requests, without modifying `.env` or other projects.

Secrets and verified Bale admin IDs live only in ignored `.env`: `BALE_BOT_TOKEN`, `BALE_BOT_USERNAME`, `BALE_ADMIN_IDS` (JSON list).

The dual process uses exactly the existing handlers, one dispatcher, one inventory database, one Sheets reconciliation loop and one stock lock. Product edits, reservations, reports and financial records share the same database; this is not a copied, independently writable stock snapshot. Existing Google Sheets write credentials are still required for two-way writes.

Bale account/chat IDs are reversibly namespaced into negative internal IDs below -10^16. Telegram IDs remain unchanged. User roles and verified contact checks use these platform-specific identities, not shared usernames or phones. Management reports may intentionally show both platforms.

Bale file IDs have a `balefile:` provenance prefix. Cross-platform media is downloaded via its source bot and uploaded to its destination, with a 30 MB transfer limit. The prefix is removed only when making a native Bale API request. Source credentials must remain available to fetch historical media.

Bale messages are converted from Telegram HTML into plain text because identical formatting is not guaranteed. Short bot referral links are converted to `ble.ir/Rinotek_bot?start=R...`. Named Bale personnel use `ble.ir/<username>` chat links; anonymous ID-only Bale chat links are omitted rather than fabricated.

External services (Sheets write bridge, AI provider, payment provider) retain their existing prerequisites. An API connection or local test does not establish that all client menus, native links and file formats have been validated in the Bale application.

Compatibility checks cover every visible main-menu entry for customer, seller, branch manager and admin through the production dispatcher with isolated test data. Transport regression checks cover media replacement, source-platform file retrieval, PDF filenames, branch-map text links and incoming callback metadata. These simulated checks are separate from the explicit live owner smoke (`python -m scripts.bale_smoke --direct-network`).

Known independent audit issue: existing catalogue JPEG bytes disagree with the older SHA-256 values in the photography manifest. The Bale work does not replace photos or silently rebaseline those hashes; the original integrity test remains enabled in normal full-suite runs.
