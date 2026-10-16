-- TEST DATA ONLY. Fills the Orders, Calendar and Insights tabs with fake orders so the bot can be tried out:
-- best seller, "where is my order?", missed orders, unanswered questions.
--
-- Run it in the Supabase SQL editor of a TEST project (not the owner's real data). Everything it adds belongs to
-- customers whose messenger_id starts with 'seed-' and is named "TEST ...", so supabase/remove_test_orders.sql
-- can delete exactly those rows afterwards.
--
-- It needs these cakes on the menu: Yema Cake, Mango Float, Leche Flan, Nutella Ferrero Cake, Chocolate Moist Cake
-- (an order whose cake is missing is skipped). Run it once; a second run stops with a message.
-- Product stock is put back exactly as it was, whatever triggers your database has on orders.

do $seed$
declare
  has_stock    boolean := exists (select 1 from information_schema.columns
                                  where table_schema = 'public' and table_name = 'products' and column_name = 'stock');
  has_fee      boolean := (select count(*) = 2 from information_schema.columns
                           where table_schema = 'public' and table_name = 'orders'
                             and column_name in ('delivery_area', 'delivery_fee'));
  has_fulfill  boolean := exists (select 1 from information_schema.columns
                                  where table_schema = 'public' and table_name = 'orders' and column_name = 'fulfillment');
  r        record;
  cust     bigint;
  oid      bigint;
  prod1    record;
  prod2    record;
  subtotal numeric;
  fee      numeric;
  delivery boolean;
begin
  if exists (select 1 from public.customers where messenger_id like 'seed-%') then
    raise exception 'Test orders are already there. Run supabase/remove_test_orders.sql first.';
  end if;

  -- Remember the stock, and lift it so that a stock check on orders can never block the test data.
  if has_stock then
    execute 'create temp table _seed_stock on commit drop as select product_id, stock from public.products';
    execute 'update public.products set stock = 1000000 where stock is not null';
  end if;

  insert into public.customers (name, contact_number, messenger_id) values
    ('TEST Ana Cruz',     '09170000001', 'seed-1'), ('TEST Ben Reyes',    '09170000002', 'seed-2'),
    ('TEST Carla Santos', '09170000003', 'seed-3'), ('TEST Dino Garcia',  '09170000004', 'seed-4'),
    ('TEST Ella Lopez',   '09170000005', 'seed-5'), ('TEST Franco Diaz',  '09170000006', 'seed-6'),
    ('TEST Gina Torres',  '09170000007', 'seed-7'), ('TEST Hanna Uy',     '09170000008', 'seed-8');

  -- c = customer number, d/h = days and hours ago, s = status, f = pickup or delivery, then up to two cakes
  for r in
    select * from jsonb_to_recordset('[
      {"c": 1, "d": 0, "h": 1, "s": "pending", "f": "delivery", "p1": "Yema Cake", "q1": 2, "p2": "Mango Float", "q2": 1},
      {"c": 2, "d": 0, "h": 3, "s": "confirmed", "f": "pickup", "p1": "Leche Flan", "q1": 2, "p2": null, "q2": null},
      {"c": 3, "d": 0, "h": 5, "s": "preparing", "f": "delivery", "p1": "Yema Cake", "q1": 1, "p2": "Nutella Ferrero Cake", "q2": 1},
      {"c": 4, "d": 1, "h": 2, "s": "ready", "f": "pickup", "p1": "Mango Float", "q1": 3, "p2": null, "q2": null},
      {"c": 5, "d": 1, "h": 6, "s": "out_for_delivery", "f": "delivery", "p1": "Yema Cake", "q1": 3, "p2": null, "q2": null},
      {"c": 6, "d": 1, "h": 9, "s": "completed", "f": "pickup", "p1": "Leche Flan", "q1": 1, "p2": "Mango Float", "q2": 1},
      {"c": 1, "d": 2, "h": 2, "s": "completed", "f": "delivery", "p1": "Yema Cake", "q1": 2, "p2": null, "q2": null},
      {"c": 7, "d": 2, "h": 7, "s": "completed", "f": "pickup", "p1": "Mango Float", "q1": 2, "p2": null, "q2": null},
      {"c": 2, "d": 3, "h": 1, "s": "completed", "f": "delivery", "p1": "Yema Cake", "q1": 4, "p2": "Leche Flan", "q2": 1},
      {"c": 8, "d": 3, "h": 8, "s": "completed", "f": "pickup", "p1": "Nutella Ferrero Cake", "q1": 1, "p2": null, "q2": null},
      {"c": 3, "d": 4, "h": 4, "s": "completed", "f": "delivery", "p1": "Mango Float", "q1": 2, "p2": "Yema Cake", "q2": 1},
      {"c": 4, "d": 5, "h": 3, "s": "completed", "f": "pickup", "p1": "Yema Cake", "q1": 2, "p2": null, "q2": null},
      {"c": 5, "d": 6, "h": 5, "s": "completed", "f": "delivery", "p1": "Leche Flan", "q1": 2, "p2": null, "q2": null},
      {"c": 6, "d": 7, "h": 2, "s": "completed", "f": "pickup", "p1": "Yema Cake", "q1": 1, "p2": "Mango Float", "q2": 2},
      {"c": 7, "d": 8, "h": 6, "s": "completed", "f": "delivery", "p1": "Nutella Ferrero Cake", "q1": 2, "p2": null, "q2": null},
      {"c": 8, "d": 9, "h": 1, "s": "completed", "f": "pickup", "p1": "Yema Cake", "q1": 3, "p2": null, "q2": null},
      {"c": 1, "d": 10, "h": 4, "s": "completed", "f": "delivery", "p1": "Mango Float", "q1": 1, "p2": "Leche Flan", "q2": 1},
      {"c": 2, "d": 11, "h": 7, "s": "completed", "f": "pickup", "p1": "Yema Cake", "q1": 2, "p2": "Chocolate Moist Cake", "q2": 1},
      {"c": 3, "d": 12, "h": 2, "s": "completed", "f": "delivery", "p1": "Yema Cake", "q1": 1, "p2": null, "q2": null},
      {"c": 4, "d": 13, "h": 3, "s": "completed", "f": "pickup", "p1": "Mango Float", "q1": 2, "p2": null, "q2": null},
      {"c": 5, "d": 14, "h": 5, "s": "completed", "f": "delivery", "p1": "Nutella Ferrero Cake", "q1": 1, "p2": "Yema Cake", "q2": 2},
      {"c": 6, "d": 16, "h": 6, "s": "completed", "f": "pickup", "p1": "Leche Flan", "q1": 2, "p2": null, "q2": null},
      {"c": 7, "d": 17, "h": 2, "s": "completed", "f": "delivery", "p1": "Yema Cake", "q1": 2, "p2": "Mango Float", "q2": 1},
      {"c": 8, "d": 19, "h": 4, "s": "completed", "f": "pickup", "p1": "Mango Float", "q1": 2, "p2": null, "q2": null},
      {"c": 1, "d": 21, "h": 3, "s": "completed", "f": "delivery", "p1": "Yema Cake", "q1": 1, "p2": "Nutella Ferrero Cake", "q2": 1},
      {"c": 2, "d": 22, "h": 8, "s": "completed", "f": "pickup", "p1": "Leche Flan", "q1": 1, "p2": null, "q2": null},
      {"c": 3, "d": 24, "h": 1, "s": "completed", "f": "delivery", "p1": "Yema Cake", "q1": 2, "p2": null, "q2": null},
      {"c": 4, "d": 26, "h": 5, "s": "completed", "f": "pickup", "p1": "Nutella Ferrero Cake", "q1": 1, "p2": "Chocolate Moist Cake", "q2": 1},
      {"c": 5, "d": 27, "h": 2, "s": "completed", "f": "delivery", "p1": "Mango Float", "q1": 1, "p2": null, "q2": null},
      {"c": 6, "d": 1, "h": 4, "s": "cancelled", "f": "pickup", "p1": "Chocolate Moist Cake", "q1": 5, "p2": null, "q2": null},
      {"c": 7, "d": 4, "h": 6, "s": "cancelled", "f": "delivery", "p1": "Leche Flan", "q1": 6, "p2": null, "q2": null},
      {"c": 8, "d": 9, "h": 3, "s": "cancelled", "f": "pickup", "p1": "Nutella Ferrero Cake", "q1": 4, "p2": null, "q2": null},
      {"c": 1, "d": 40, "h": 2, "s": "completed", "f": "pickup", "p1": "Chocolate Moist Cake", "q1": 9, "p2": null, "q2": null}
    ]'::jsonb) as x(c int, d int, h int, s text, f text, p1 text, q1 int, p2 text, q2 int)
  loop
    select product_id, price into prod1 from public.products where name = r.p1;
    if not found then continue; end if;
    select product_id, price into prod2 from public.products where name = r.p2;
    select customer_id into cust from public.customers where messenger_id = 'seed-' || r.c;

    delivery := r.f = 'delivery';
    fee := case when delivery and has_fee then 50 else 0 end;
    subtotal := prod1.price * r.q1 + case when prod2.product_id is not null then prod2.price * r.q2 else 0 end;

    insert into public.orders (customer_id, order_date, status, total_amount, location)
    values (cust, now() - make_interval(days => r.d, hours => r.h),
            case when r.s in ('pending', 'completed', 'cancelled') then r.s else 'pending' end,
            subtotal + fee,
            case when r.f = 'delivery' then 'Test Street ' || r.c || ', JARO' else 'Pick up at our shop' end)
    returning order_id into oid;

    insert into public.order_items (order_id, product_id, quantity, unit_price, subtotal)
    values (oid, prod1.product_id, r.q1, prod1.price, prod1.price * r.q1);
    if prod2.product_id is not null then
      insert into public.order_items (order_id, product_id, quantity, unit_price, subtotal)
      values (oid, prod2.product_id, r.q2, prod2.price, prod2.price * r.q2);
    end if;

    if has_fee then
      execute format('update public.orders set delivery_area = %L, delivery_fee = %s where order_id = %s',
                     case when delivery then 'JARO' end, fee, oid);
    end if;
    if has_fulfill then
      execute format('update public.orders set fulfillment = %L where order_id = %s', r.f, oid);
    end if;

    -- confirmed / preparing / ready / out_for_delivery only exist if this database allows those statuses
    if r.s not in ('pending', 'completed', 'cancelled') then
      begin
        execute format('update public.orders set status = %L where order_id = %s', r.s, oid);
      exception when check_violation then
        null;  -- the order simply stays 'pending'
      end;
    end if;
  end loop;

  -- For the Insights tab: customers who tried to order while the shop was closed...
  if to_regclass('public.missed_orders') is not null then
    insert into public.missed_orders (messenger_id, reason, created_at) values
      ('seed-1', 'hours', now() - interval '3 hours'), ('seed-2', 'hours', now() - interval '5 hours'),
      ('seed-3', 'day',   now() - interval '1 day'),   ('seed-4', 'day',   now() - interval '1 day 2 hours'),
      ('seed-5', 'hours', now() - interval '2 days'),  ('seed-6', 'switch', now() - interval '3 days'),
      ('seed-7', 'hours', now() - interval '4 days'),  ('seed-8', 'day',   now() - interval '6 days');
  end if;

  -- ...and questions the bot could not answer
  insert into public.owner_inbox (messenger_id, kind, message, status, created_at) values
    ('seed-1', 'unanswered', 'do you have ube cake?', 'open', now() - interval '1 day'),
    ('seed-2', 'unanswered', 'Do you have ube cake?', 'open', now() - interval '2 days'),
    ('seed-3', 'unanswered', 'ube cake meron?',       'open', now() - interval '3 days'),
    ('seed-4', 'unanswered', 'is it gluten free?',    'open', now() - interval '2 days'),
    ('seed-5', 'unanswered', 'is it gluten free?',    'open', now() - interval '5 days'),
    ('seed-6', 'unanswered', 'can I get a discount?', 'open', now() - interval '4 days');

  if has_stock then
    execute 'update public.products p set stock = s.stock from _seed_stock s where p.product_id = s.product_id';
  end if;
end
$seed$;
