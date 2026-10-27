# MyBudget (web)

*You've got to have a plan...*

MyBudget is a household budget app that runs on your own computer. All data stays in a SQLite file, `data/mybudget.db`.

## Features

- **Dashboard:** what's left to spend, a safe daily amount, savings rate, a 6-month cash-flow chart, where the money went, upcoming bills and goals.
- **Transactions:** search and filters, who entered each item, repeating monthly entries, CSV export and import.
- **Import statement:** upload a bank or card statement as CSV, OFX or QFX.
  - Picks out income and expenses and tags them to a person.
  - Suggests a category for each line, learning from your earlier choices.
  - Skips duplicates and card-payment transfers.
- **Budgets:** fixed and flexible categories, showing spent − budget = remaining, a marker for where you should be by today, and the 3-month average.
- **Goals:** a target and due date, how much to save each month, and on-track status. Moving money into a goal counts as a transfer, not an expense.
- **Bills:** a calendar and automatic detection of recurring charges.
- **Accounts:** balances, net worth, owner of each account, and income and expenses per person.
- **Insights:** rule-based tips plus optional AI advice from Claude.
- **Sign in with Google:** only the accounts you list can get in.

## Run it

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Copy `.env.example` to `.env` and fill it in. Leave the Google fields empty to run without a login.
3. Double-click **MyBudget** on the Desktop, or `MyBudget.command` in this folder. It does three things:
   - Starts Docker if it isn't running.
   - Builds and starts the container.
   - Opens <http://localhost:8765>.

From a terminal instead:

```bash
docker compose up -d --build   # start
docker compose down            # stop
```

To run without Docker (Python 3.10 or newer, no other packages needed):

```bash
python3 app/server.py
```

## Set up Google sign-in

1. Go to [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
2. Create a project.
3. Open **OAuth consent screen**:
   - Choose **External**.
   - Add your app name.
   - Add your household's emails as **test users**.
4. Open **Credentials → Create credentials → OAuth client ID**:
   - Choose **Web application**.
   - Add the authorized redirect URI `http://localhost:8765/auth/callback`.
5. Put the client ID and secret into `.env`.
6. List everyone allowed in under `ALLOWED_EMAILS`.
7. Run `docker compose up -d` again.

## Import a bank statement

1. In your bank's website, look for **Download transactions** or **Export** and choose CSV, or OFX/QFX (Quicken). PDF statements aren't supported.
2. In MyBudget, open **Import statement** and fill in:
   - The file.
   - The account or card.
   - Whose statement it is.
3. Check the columns, amount signs and categories it detected, then click **Import**.

## Data and backups

- Everything is in `data/mybudget.db`. Copy that file to back it up.
- The `data/` folder and `.env` are git-ignored, so your money and secrets never reach the repository.

## Project layout

```
app/server.py        Python standard-library HTTP server, SQLite, Google OAuth, Claude API
app/index.html       the whole web app (vanilla JS + Chart.js)
Dockerfile           python:3.12-slim image
docker-compose.yml   port 8765 on localhost, ./data mounted at /data
MyBudget.command     macOS launcher (starts Docker, the container and the browser)
```
