-- Removes exactly what supabase/seed_test_orders.sql added: the "TEST ..." customers whose messenger_id starts with
-- 'seed-', their orders and items, and the fake missed orders and unanswered questions. Real data is not touched.
-- Product stock is put back exactly as it was, whatever triggers your database has on orders.

do $clean$
declare
  has_stock boolean := exists (select 1 from information_schema.columns
                               where table_schema = 'public' and table_name = 'products' and column_name = 'stock');
begin
  if has_stock then
    execute 'create temp table _seed_stock on commit drop as select product_id, stock from public.products';
  end if;

  if to_regclass('public.missed_orders') is not null then
    delete from public.missed_orders where messenger_id like 'seed-%';
  end if;
  delete from public.owner_inbox where messenger_id like 'seed-%';
  delete from public.orders where customer_id in (select customer_id from public.customers where messenger_id like 'seed-%');
  delete from public.customers where messenger_id like 'seed-%';

  if has_stock then
    execute 'update public.products p set stock = s.stock from _seed_stock s where p.product_id = s.product_id';
  end if;
end
$clean$;
