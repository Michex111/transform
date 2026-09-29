// The floating mini chat's conversation, held for the browser session.
//
// Module-level on purpose: the launcher's panel unmounts when it is closed, so
// a transcript kept in React state would be thrown away and the next follow-up
// would start from scratch. One store for the whole tab keeps the thread,
// including its `conversationId`, across open/close and across route changes —
// and it never leaves the tab, so nothing is persisted or shared between
// accounts. `AssistantLauncher` clears it when the signed-in identity changes.

import { createAssistantChatStore } from "@/lib/assistantChatStore";

export const miniAssistantStore = createAssistantChatStore();
