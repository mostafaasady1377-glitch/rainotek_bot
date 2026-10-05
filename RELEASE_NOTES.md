# RAINOTEK current release

This release includes separate customer, seller, branch-manager and management panels;
fixed management-account allowlisting; branch-scoped reports; short referral links;
product-edit permissions and expiring confirmations; and a private financial archive.

Customer contact sharing is deferred until the first action after opening the menu.
The customer shares their own Telegram contact once and then selects the action again.

## Deployment boundaries

- Runtime secrets and management account configuration remain in local `.env`.
- Use a JSON array for `ADMIN_TELEGRAM_IDS` when configuring several accounts.
- The live database, customer information and financial records are not stored in Git.
- Google Sheets reads use polling. Writes require a deployed bridge URL and secret.
  Without these credentials, price/specification writes fail closed; photos remain local.
- Financial documents are an archive, not a payment/cheque-clearing accounting ledger.
- Existing source-controlled product images remain part of the current release.
- Git history is retained; retired local database backups are moved outside the project.
