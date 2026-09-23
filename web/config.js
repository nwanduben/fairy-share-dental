// Joy's public ElevenLabs agent id. Replace the placeholder, commit, redeploy.
//
// The id is public by design — the browser sends it to start a conversation.
// What keeps the demo from being abused is set in the ElevenLabs dashboard:
//   - allowlist the deployed host, so only this site can start a conversation
//   - cap concurrent conversations and conversations per day
//   - cap the length of a single conversation
// Create or update the agent with: elevenlabs/provision_agent.py
window.FSD_AGENT_ID = 'YOUR_ELEVENLABS_AGENT_ID';
