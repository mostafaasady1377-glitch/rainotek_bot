# RAINOTEK — stable snapshot, 2026-10-17

This snapshot preserves the current customer, expert, branch-manager and owner panels.

- Ad previews show one photo with previous/next navigation across the 15-photo gallery, followed by the ad text and actions.
- Experts can edit their own ad title and description. Edits survive refresh while inventory is unchanged; changed inventory invalidates the edited version and old transfer links.
- Root role panels reopen at the bottom of the conversation and remove legacy reply keyboards. Customer and staff access remain separate.
- Referral links retain Telegram's standard `?start=R…` format.
- The local browser handoff and extension are included. Automatic completion of every Divar registration step through payment has **not** been verified live. Final registration and payment remain with the account owner.

## Run

Install `requirements.txt`, copy `.env.example` to `.env`, and configure credentials locally. Never commit `.env`, service-account credentials, databases, browser sessions or the `scratch` directory.

Run `python main.py` for Telegram, or `python main_dual.py` for Telegram and Bale. Do not run both simultaneously. For Bale configuration see `BALE_RUNTIME.md`.

For local handoff run `python -m divar_campaign.handoff_server`; follow `docs/DIVAR_LOCAL.md`. Each expert uses their own authenticated Divar browser. Remote experts need a reachable HTTPS handoff service.

The current personal suggestions are generated from live inventory. The older campaign pack is runtime data under `scratch/divar-campaign/pack-100`; regenerate it with `python -m divar_campaign.build_pack` when using the legacy campaign queue. Inventory and credentials are deliberately excluded from Git.
