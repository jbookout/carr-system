import { randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { withPostgresFixture } from './disposable-postgres.mjs';
import { restoreEventIdentity } from './snapshot-schema.mjs';

export async function withLeadVerbFixture(fn) {
  return withPostgresFixture({ tables: ['actor', 'lead', 'lead_stage', 'lead_lane', 'event', 'tool_call', 'activity'].map(name => `public.${name}`) }, async ({ c, connect, command: invoke }) => {
    const schema = readFileSync(new URL('../../../db/schema.sql', import.meta.url), 'utf8');
    await restoreEventIdentity(c, schema);
    await c.query(schema.match(/CREATE FUNCTION public.trg_touch_row\(\)[\s\S]*?end \$\$;/)[0]);
    await c.query(`alter table tool_call add primary key(idempotency_key);
      create trigger lead_touch before update on lead for each row execute function trg_touch_row();
      create view v_ref_index as select 'lead'::text subject_type,id subject_id,registry_ref ref from lead;
      insert into lead_stage(slug,label) values ('new','New'),('qualified','Qualified'),('engaged','Engaged'),('outreach_active','Outreach'),('nurture_drip','Nurture'),('opportunity','Opportunity'),('active_deal','Active deal'),('closed_won','Won'),('closed_lost','Lost'),('do_not_contact','Do not contact'),('archived','Archived');
      insert into lead_lane(slug,label) values ('primary','Primary');`);
    const actor = { id: randomUUID(), slug: 'joe', human: true };
    const lead = randomUUID();
    await c.query("insert into actor(id,slug,kind,display_name) values($1,'joe','human','Synthetic partner')", [actor.id]);
    await c.query("insert into lead(id,party_id,registry_ref,stage,notes,created_by,updated_by) values($1,$2,'L-1','new','Original synthetic note',$3,$3)", [lead, randomUUID(), actor.id]);
    const command = async (client, extra = {}, caller = actor) => {
      return invoke(client, caller, 'update-lead', { lead, idempotency_key: randomUUID(), base_version: 1, fields: { notes: 'Revised synthetic note' }, ...extra });
    };
    await fn({ c, connect, actor, lead, command });
  });
}

