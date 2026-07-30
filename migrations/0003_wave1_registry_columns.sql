-- ============================================================================
-- 0003: Wave 1 column reconciliation against the LIVE registry (read 7/30)
-- The registry's full 26-column header exposed fields the v2 schema had no
-- home for. Every column gets a home before import; nothing is dropped at
-- import time. The vault schema file stays frozen as the v2 design record
-- (migrations/README rule); this is the delta.
-- ============================================================================

-- party: attributes that belong to the person/practice, not the lead lifecycle
alter table party add column specialty text;         -- 'Specialty' (GP, ortho, PT, OD...)
alter table party add column county text;            -- 'County' (radar + search anchor)

-- lead: registry lifecycle fields
alter table lead rename column source to source_type;            -- 'Source Type'
alter table lead add column source_detail text;                  -- 'Source Detail (V-ID / event / referrer)'
alter table lead add column segment text;                        -- 'Segment' (board segment)
alter table lead add column report_back_due date;                -- 'Report-Back Due'
alter table lead add column drip_campaign text;                  -- 'Drip Campaign'
alter table lead add column drip_added date;                     -- 'Drip Added'
alter table lead add column sf_deal text;                        -- 'SF Deal' (resolves to deal.salesforce_id post-import)
alter table lead add column notes_path text;                     -- 'Detail File'
alter table lead add column notes text;                          -- 'Notes' (short prose; long prose stays in the detail file)
alter table lead add column event_source text;                   -- 'Event-Source' (renewal radar provenance)
alter table lead add column event_confidence text;               -- 'Event-Confidence' (kept as-is from the radar)
