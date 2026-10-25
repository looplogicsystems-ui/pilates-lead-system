-- Migration 010: retire the old duplicate copies of prices and schedule.
--
-- APPLY TOGETHER WITH THE NEW LEAD BRAIN (pilot build step 4.6), not before:
-- the Lead Brain that ran until then reads prompt_vars.offer / .classes and
-- studios.offers to build its prompt, so retiring them early blanks the live
-- demo's prices.
--
-- Prices and schedule lived in three places (studios.offers,
-- studios.class_schedule, prompt_vars) and disagreed (intro $89 vs $49,
-- 5-pack $155 vs $145, unlimited $199 vs $189, drop-in $34 vs $32). From here
-- studio_offers and studio_classes (migration 009) are the only source.
-- Safe to re-run.
--
-- ROLLBACK: restore studios from the llctl snapshot taken before the deploy
--           (the removed values are not reconstructible from this file);
--           DELETE FROM schema_migrations WHERE version = '010_retire_old_price_fields';

-- Retire the old duplicate copies of prices and schedule. They lived in three
-- places (studios.offers, studios.class_schedule, prompt_vars) and disagreed
-- (intro $89 vs $49, 5-pack $155 vs $145, ...). studio_offers and
-- studio_classes are now the only source. Safe to re-run.
UPDATE studios SET
    offers = NULL,
    class_schedule = NULL,
    prompt_vars = prompt_vars - 'offer' - 'class_pack' - 'membership' - 'single_class_price'
                              - 'classes' - 'schedule_summary' - 'booking_url' - 'booking_mode'
                              - 'currency' - 'currency_symbol' - 'class_duration_min' - 'owner_name'
WHERE id = 2;

-- One voice example quoted a price ($49) and specific times; the model copies
-- examples, so it would quote them. Replace that one example (only if still the old text).
UPDATE studios
   SET prompt_vars = jsonb_set(prompt_vars, '{voice,examples,0}',
       to_jsonb('Hey Sarah! So glad you found us. The intro pass is honestly the best way to figure out if reformer clicks for you — want me to find you a class this week?'::text))
 WHERE id = 2
   AND prompt_vars #>> '{voice,examples,0}' LIKE '%$49%';

INSERT INTO schema_migrations (version) VALUES ('010_retire_old_price_fields')
ON CONFLICT (version) DO NOTHING;
