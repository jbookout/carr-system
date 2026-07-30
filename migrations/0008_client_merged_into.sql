-- 0008: client merge tombstones. The live roster encodes merges as status
-- strings ("Merged into C-008"); rehearsal import surfaced 8 of them. Same
-- pattern as party/building (A3): merge = pointer write.
alter table client add column merged_into uuid references client(id);
create index client_merged_idx on client (merged_into) where merged_into is not null;
