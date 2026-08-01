-- Migration 006: seed studio 1 (Reform Collective Pilates) from the values that
-- were hardcoded in the workflow.
--
-- The live `studios` row had never actually been filled in — it still held the
-- template values ('Your Studio Name', 'City, State', '2-week unlimited intro
-- pass for 35 USD') from the original setup script, while the AI system prompt
-- carried the real persona as string literals. Nothing read the table, so the
-- drift was invisible. Single-sourcing it here is what makes that impossible.
--
-- Currency: PKR 3,500 (the studio is in Lahore). The '35 USD' in the old row
-- was template residue and is the value the development critique flagged.
--
-- Only public, non-environment values live here. Calendar IDs, Slack channel
-- IDs and the Instagram account ID are deployment-specific and are applied
-- separately from `db/seed/local-ids.sql` (gitignored) — see
-- `db/seed/local-ids.sql.example`.
--
-- ROLLBACK: no clean automatic rollback (this overwrites row values).
--   Restore from the pre-migration dump if needed.
--   DELETE FROM schema_migrations WHERE version = '006_seed_reform_collective';

INSERT INTO studios (
    id, name, location, studio_type, offers, class_schedule,
    timezone, class_capacity, booking_duration_minutes, prompt_vars
) VALUES (
    1,
    'Reform Collective Pilates',
    'Gulberg, Lahore',
    'Pilates',
    '2-week unlimited intro pass for PKR 3,500 (first-timers only)',
    'Mon/Wed/Fri 7:00am & 6:30pm, Tue/Thu 9:00am & 5:30pm, Sat 10:00am (reformer & mat)',
    'Asia/Karachi',
    8,
    50,
    jsonb_build_object(
        'persona_name',   'Mia',
        'persona_role',   'the owner',
        'studio_name',    'Reform Collective Pilates',
        'studio_type',    'boutique reformer & mat Pilates studio',
        'location',       'Gulberg, Lahore',
        'city',           'Lahore',
        'classes',        'Reformer Flow, Mat Pilates, and Beginner Reformer',
        'offer',          '2-week unlimited intro pass for PKR 3,500 (first-timers only)',
        'amenities',      'Free parking',
        'timezone_label', 'Pakistan Standard Time',
        'voice',          'warm, casual, and human, like you''re texting someone curious about trying a class'
    )
)
ON CONFLICT (id) DO UPDATE SET
    name                     = EXCLUDED.name,
    location                 = EXCLUDED.location,
    studio_type              = EXCLUDED.studio_type,
    offers                   = EXCLUDED.offers,
    class_schedule           = EXCLUDED.class_schedule,
    timezone                 = EXCLUDED.timezone,
    class_capacity           = EXCLUDED.class_capacity,
    booking_duration_minutes = EXCLUDED.booking_duration_minutes,
    prompt_vars              = EXCLUDED.prompt_vars;

-- Keep the SERIAL in step after an explicit-id insert, or the next studio
-- inserted through the UI collides on the primary key.
DO $$
BEGIN
    PERFORM setval(
        pg_get_serial_sequence('studios', 'id'),
        GREATEST((SELECT MAX(id) FROM studios), 1)
    );
END $$;

INSERT INTO schema_migrations (version) VALUES ('006_seed_reform_collective')
ON CONFLICT (version) DO NOTHING;
