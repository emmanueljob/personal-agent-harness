# Visitors and conversations

Open `/admin`, sign in with the owner password, and choose **Visitors &
conversations**. On each browser you use, select **Mark this browser as mine**.
This authenticated setting excludes that browser's arrivals and classifies its
existing and future cookie-attributed conversations as owner activity. Use **Stop
excluding this browser** to undo it. Signing out does not remove the marker.

The conversation view defaults to other browsers. Select owner, unknown/legacy,
or all conversations as needed. Anonymous browser IDs connect arrivals to saved
conversations. Search and inspect prompts, replies and recorded execution details.
Lists and the selected conversation refresh every ten seconds; older conversation
pages can be loaded separately. The usage metrics above the log include all
traffic, independently of the conversation filter.

Select **Enable browser alerts** and allow notifications in your browser. New
arrivals generate a generic notification without conversation content. Existing
arrivals do not trigger a backlog of notifications. Keep this dashboard open;
closing it, navigating away or signing out stops polling and alerts. Browser and
OS notification settings, sleep and background-tab throttling can delay delivery.
There is no web push service or email delivery in this feature.

This recognizes browsers, not people. A cleared/expired cookie or another device
looks like a new visitor. Shared browsers share attribution. Outreach labels are
not verified identities, and forwarded links cannot prove who opened them. Local
or historical chats without a visitor cookie mapping remain unknown. Local and
hosted application data are separate.

Arrivals use the existing HTTP-only 30-day conversation cookie and a server-side
hash, never IP addresses or fingerprinting. A page view after 30 minutes without
another page view starts a new arrival. Tracking settings, DNT and GPC apply to
arrival collection. Up to 100 recent arrivals appear in the dashboard. Storage is
pruned on arrival writes to 30 days and 50,000 records. Owner browser markers are
retained until explicitly removed; they only identify a browser while it still
has its cookie. Conversation retention is unchanged.

The additive SQLite schema creates `owner_browsers` and `visitor_arrivals` on
application startup. It preserves existing tables and workflow histories. No new
dependencies, workflow changes, provider calls or external analytics identifiers
are introduced. The existing public-chat cookie authorization remains required.
