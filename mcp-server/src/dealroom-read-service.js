// One canonical Deal Room projection reader shared by the MCP board and typed
// browser reads. Filters are supplied only by server-owned callers; browser
// query input never reaches this function.
export async function readDealRoomDeals(client, {
  workspace = "all",
  accountClientId = null,
  owner = null,
  operatingState = null,
} = {}) {
  const result = await client.query(
    `select id, name, type, phase, owner, attention,
            to_jsonb(next_date)#>>'{}' as next_date, next_step, market, segment,
            client_id, client_ref, client_name, account_client_id, account_client_ref,
            account_name, account_owner, market_agent,
            to_jsonb(last_touch)#>>'{}' as last_touch,
            to_jsonb(last_review_at)#>>'{}' as last_review_at, workspace_kind,
            operating_state, parking_reason, parking_note,
            to_jsonb(parked_at)#>>'{}' as parked_at, parked_by
       from v_deal_room_board
      where ($1 = 'all' or workspace_kind = $1)
        and ($2::uuid is null or account_client_id = $2::uuid)
        and ($3::text is null or owner = $3::text)
        and ($4::text is null or operating_state = $4::text)
      order by attention desc, next_date nulls last, name`,
    [workspace, accountClientId, owner, operatingState],
  );
  return result.rows;
}
