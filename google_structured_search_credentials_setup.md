# Google structured-search credentials for the Thatcher evidence tool

> **Historical backend note:** This guide applies only to the earlier Google
> Custom Search JSON / Programmable Search backend. It does not configure the
> later Google Discovery Engine website-search implementation; the two
> backends are distinct.

The research programme uses Google's ordinary Custom Search JSON API. It does
not use Gemini, an AI-search API, browser automation, or scraped Google result
pages.

## 1. Create or select a Google Cloud project

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a dedicated project, or select an existing project intended for this
   research.
3. Enable billing if Google requires it for the Custom Search JSON API.
4. In **APIs & Services → Library**, find and enable **Custom Search API**.

Google's overview is at:
https://developers.google.com/custom-search/v1/overview

## 2. Create a Programmable Search Engine

1. Open the [Programmable Search Engine control panel](https://programmablesearchengine.google.com/controlpanel/all).
2. Create a search engine suitable for ordinary web discovery. Configure it to
   search the web rather than only a small hand-picked site list, if that option
   is available for the account.
3. In the engine's **Basics** or **Overview** settings, copy the **Search engine
   ID**. Google also calls this value `cx`.

The search-engine ID is not the API key. The tool needs both.

## 3. Create and restrict an API key

1. In the Cloud Console, open **APIs & Services → Credentials**.
2. Choose **Create credentials → API key**.
3. Edit the key and apply an **API restriction** allowing only the **Custom
   Search API**.
4. Where practical, add an application restriction appropriate to `big-nas-2`,
   such as its stable public egress IP. Do not apply an IP restriction unless
   that egress address is genuinely stable.
5. Set a Google Cloud budget alert as an additional safeguard. The local tool
   independently refuses to exceed US$5 of conservatively estimated search
   cost.

Never paste the API key into chat, a report, a test fixture, or a tracked file.

## 4. Record Google's current price

Check the current Custom Search JSON API quota and pricing shown by Google for
the project. Do not assume a historical price. Record the price in US dollars
per 1,000 requests as a plain decimal, for example `5.00` only if Google
currently quotes US$5 per 1,000 for this account.

If the remaining free quota is not known exactly at the start of this run, set
it to `0`. That makes the cost estimate conservative.

## 5. Add the four settings locally

Edit this ignored secrets file on `big-nas-2`:

```text
/disks/disk1/etc/mrsMThatcher/mrsMThatcher.env
```

Add:

```dotenv
GOOGLE_CUSTOM_SEARCH_API_KEY=replace_with_the_restricted_api_key
GOOGLE_CUSTOM_SEARCH_ENGINE_ID=replace_with_the_cx_search_engine_id
HISTORICAL_SEARCH_USD_PER_1000_REQUESTS=replace_with_current_decimal_price
HISTORICAL_SEARCH_FREE_REQUESTS_REMAINING=0
```

Keep the file private:

```bash
chmod 600 /disks/disk1/etc/mrsMThatcher/mrsMThatcher.env
```

The final free-quota line is optional; the programme defaults to zero. Do not
claim free requests unless their remaining number is known for this run.

## 6. Check only that the names exist

These checks print no secret values:

```bash
cd /disks/disk1/etc/mrsMThatcher
grep -Eq '^[[:space:]]*(export[[:space:]]+)?GOOGLE_CUSTOM_SEARCH_API_KEY=' mrsMThatcher.env && echo 'API key name present'
grep -Eq '^[[:space:]]*(export[[:space:]]+)?GOOGLE_CUSTOM_SEARCH_ENGINE_ID=' mrsMThatcher.env && echo 'search engine ID name present'
grep -Eq '^[[:space:]]*(export[[:space:]]+)?HISTORICAL_SEARCH_USD_PER_1000_REQUESTS=' mrsMThatcher.env && echo 'price name present'
```

Do not run a command which prints the environment file or echoes the values.

## 7. Run the research only when the tool review is complete

The paid/resumable command is:

```bash
cd /disks/disk1/etc/mrsMThatcher
python3 historical_context_search_research.py resume --execute-search
```

Use `run --execute-search` instead only when no run-state file exists. The
programme will not scrape Google HTML and will stop rather than substitute an
AI search provider. It enforces 120 planned unique queries, 150 total request
attempts including confirmed retries, a US$5 search-cost hard stop, and a
400-URL fetch hard stop.

If Google no longer offers a usable Custom Search JSON/Programmable Search
configuration for this account, leave the credentials unset. The required and
safe result is then `SEARCH BACKEND NOT CONFIGURED`.
