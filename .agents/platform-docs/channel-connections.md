# channel-connections

> MUST call for WhatsApp or WhatsApp group questions; setup/use Telegram, WhatsApp groups (agent-created, invite link, max 8 numbers), LINE, and iMessage

You can be connected to messaging channels so users can message you directly.
When the user asks to connect a channel, walk them through the steps below — they may not be technical.

## Telegram Setup

Telegram setup tools are only available when the app owner is chatting directly. If the requester is not the owner, tell them the owner must connect Telegram from the agent editor or an owner chat.

**Preferred: 1-click bot creation**
1. Call the setup_telegram_connection tool WITHOUT a token — it will generate a creation link.
2. Share the link with the user. They click it, confirm the bot name in Telegram, and come back.
3. The bot is connected automatically within seconds.

**Fallback: manual BotFather setup**
If the user already has a bot or the 1-click flow doesn't work:
1. Walk them through @BotFather: /newbot → pick name → pick username → copy token
2. Call the setup_telegram_connection tool — it will show a form for the token.

## WhatsApp

When the owner asks to connect WhatsApp, direct them to the agent editor's WhatsApp tab, which exposes a QR / open-in-WhatsApp activation flow — WhatsApp setup happens there, not from chat. When connected, scheduled or triggered automation runs can send messages to the user on WhatsApp using the broadcast_message tool with channels=["whatsapp"]. In normal chat, do not promise a separate proactive WhatsApp send; answer in the active conversation instead.

## WhatsApp Groups

WhatsApp group UI appears only when available for the workspace/environment. Before creating a group, the owner must connect their own 1:1 WhatsApp number and send the agent a direct WhatsApp message (DM) so their number is linked. When WhatsApp groups are enabled for the app, the create-group tool is available on any interactive owner surface (web chat or a WhatsApp DM), so the owner can create their first group before any group room exists; if they aren't enabled the tool isn't registered, so tell the owner the WhatsApp group surface is unavailable rather than attempting it. The group-management tools — update group response mode and share connector setup links — are only available inside an existing WhatsApp group conversation. Non-owners are refused automatically. For OAuth from a WhatsApp group, use share_connector_setup_link instead of request_oauth_authorization, because the normal OAuth tool needs a builder UI button. Base44 WhatsApp group limits: the agent always creates a new group, shares the invite link, cannot be added to an existing WhatsApp group, and each agent-created group is limited to 8 numbers. Only if the user asks why, say this is a WhatsApp limitation.

## LINE

LINE is set up through the agent editor when the LINE tab is visible. The user generates an activation code, scans/adds the LINE account as a friend, and sends the code; the code can be copied/regenerated and expires. If the LINE tab is hidden, explain that LINE is not currently available for this workspace/environment. When connected, scheduled or triggered automation runs can send messages to the user on LINE using the broadcast_message tool with channels=["line"]. In normal chat, do not promise a separate proactive LINE send; answer in the active conversation instead.

## iMessage

iMessage is set up through the agent editor's iMessage tab (not via chat). Users generate an activation code, text it to the displayed iMessage number, and are then connected to the agent. Apple devices can open the SMS/iMessage compose flow directly; non-Apple devices show a manual fallback. The tab can show connected phone/status, disconnect, and capacity warnings. When connected, scheduled or triggered automation runs can send messages to the user on iMessage using the broadcast_message tool with channels=["imessage"]. In normal chat, do not promise a separate proactive iMessage send; answer in the active conversation instead.