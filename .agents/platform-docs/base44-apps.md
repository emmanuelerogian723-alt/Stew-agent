# base44-apps

> Listing, editing, checking, and calling the user's Base44 apps/sites (no app creation — that routes to the builder)

When available to the chatting platform user, native Base44 platform tools let the Superagent operate on the user's builder apps without sandbox CLI login. Use `list_base44_apps` to find apps/sites, `send_message_to_builder` to edit an existing app/site through the Base44 builder, `get_base44_app_status` to check progress, and `call_base44_backend_function` to invoke a deployed backend function in one of the user's apps. There is no tool to create a new Base44 app/site — when the user wants to create one, direct them to the Base44 builder at https://app.base44.com.

These tools may be unavailable in a given chat. The available case: use the native tools directly and make at most one mutating builder call per user request, because edits are metered and rate-limited; wait for status before sending another builder message. The unavailable case: tell the user direct Base44 app/site editing is not available from this agent session and direct them to open the app/site in the Base44 builder at https://app.base44.com. Do not invent `/api/.../edit-site` routes, do not use sandbox CLI auth/device login to edit Base44 apps/sites, and do not promise that the agent can edit a site unless the native tools are actually present.
