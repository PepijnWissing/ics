# Frameo Calendar

Automatically merges multiple iCloud calendars into one ICS feed for Frameo.

## How it works

A GitHub Action downloads your iCloud ICS feeds every 30 minutes, merges
them into a single `combined.ics`, and publishes it via GitHub Pages. Frameo
points to that one URL and always sees all calendars.

The workflow is triggered by an external cron service (cron-job.org) rather
than GitHub's built-in scheduler, which is unreliable at high frequency.

## Setup

### 1. Add your iCloud ICS URLs as GitHub Secrets

In your repository: **Settings → Secrets and variables → Actions**

Create four secrets:

| Secret  | Value |
|---------|-------|
| `ICAL1` | First iCloud ICS URL |
| `ICAL2` | Second iCloud ICS URL |
| `ICAL3` | Third iCloud ICS URL |
| `ICAL4` | Fourth iCloud ICS URL |
| `ICAL5` | Fifth iCloud ICS URL |

**How to find an iCloud ICS URL:**  
Open Calendar on Mac → right-click a calendar → *Share Calendar* → enable
*Public Calendar* → copy the URL shown (starts with `webcal://`; replace
`webcal://` with `https://`).

### 2. Enable GitHub Pages

**Settings → Pages → Source → Deploy from branch**  
Branch: `main` / Folder: `/docs`

### 3. Run the workflow once manually

**Actions → Update calendar → Run workflow**

### 4. Add the URL to Frameo

```
https://<your-username>.github.io/<repo-name>/combined.ics
```

## Features

- Deduplicates events by UID, including recurring-event exceptions (RECURRENCE-ID)
- Preserves VTIMEZONE components so times display correctly in all clients
- Skips a feed that is temporarily unreachable instead of wiping the output
- Only commits a new `combined.ics` when the content actually changed
- iCloud URLs are stored as Secrets and never appear in the repository

## Scheduling (cron-job.org)

GitHub's built-in `schedule:` trigger is frequently delayed by hours. Instead,
[cron-job.org](https://cron-job.org) calls the GitHub API every 30 minutes to
dispatch the workflow.

### Setup

**1. Create a GitHub Fine-Grained Personal Access Token**

GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token

- Repository access: only `PepijnWissing/ics`
- Permission: **Actions** → Read and write

**2. Create the cron job on cron-job.org**

| Field                         | Value                                                                                             |
|-------------------------------|---------------------------------------------------------------------------------------------------|
| URL                           | `https://api.github.com/repos/PepijnWissing/ics/actions/workflows/update-calendar.yml/dispatches` |
| Schedule                      | Every 30 minutes                                                                                  |
| Request method                | POST                                                                                              |
| Header `Authorization`        | `Bearer <your-token>`                                                                             |
| Header `Accept`               | `application/vnd.github+json`                                                                     |
| Header `X-GitHub-Api-Version` | `2022-11-28`                                                                                      |
| Request body (JSON)           | `{"ref":"main"}`                                                                                  |

A successful trigger returns **HTTP 204**. You can verify this in cron-job.org's execution history.

## Running locally

```bash
pip install -r requirements.txt
ICAL1="https://..." ICAL2="https://..." ICAL3="https://..." ICAL4="https://..." ICAL5="https://..." python merge.py
```

## Running tests

```bash
pip install -r requirements.txt
pytest
```
