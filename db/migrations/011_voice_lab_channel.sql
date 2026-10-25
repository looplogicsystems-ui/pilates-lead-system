-- Migration 011: the voice-lab channel.
--
-- Ops: Voice Lab (POST /webhook/ops/voice-lab, token-gated) runs messages
-- through the real Lead Brain as leads on channel 'test', so the AI's voice can
-- be tuned over whole conversations without anyone sending DMs. This row routes
-- that channel to the fictional demo studio (id 2). Lead Brain sends the owner
-- emails for 'test' leads to Resend's sink, not to a real inbox.
-- Report queries should exclude channel = 'test'.
--
-- ROLLBACK: DELETE FROM channel_accounts WHERE channel = 'test' AND account_ref = 'voice-lab';
--           DELETE FROM schema_migrations WHERE version = '011_voice_lab_channel';

INSERT INTO channel_accounts (studio_id, channel, account_ref, active)
SELECT 2, 'test', 'voice-lab', true
 WHERE EXISTS (SELECT 1 FROM studios WHERE id = 2)
ON CONFLICT (channel, account_ref) DO NOTHING;

INSERT INTO schema_migrations (version) VALUES ('011_voice_lab_channel')
ON CONFLICT (version) DO NOTHING;
