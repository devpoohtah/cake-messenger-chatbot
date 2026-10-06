-- Mrs Brave's Cake - database schema (Supabase PostgreSQL)
-- Run in the Supabase SQL Editor. Safe to re-run (idempotent).

-- ---------------------------------------------------------------
-- Shared trigger: keep updated_at current
-- ---------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------
create table if not exists public.customers (
  customer_id     bigint generated always as identity primary key,
  name            text not null check (length(btrim(name)) > 0),
  contact_number  text not null check (length(btrim(contact_number)) > 0),
  messenger_id    text unique,          -- NULL allowed (e.g. manual orders)
  created_at      timestamptz not null default now()
);

create table if not exists public.products (
  product_id    bigint generated always as identity primary key,
  name          text not null unique check (length(btrim(name)) > 0),
  description   text,
  price         numeric(10,2) not null check (price >= 0),
  is_available  boolean not null default true,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table if not exists public.orders (
  order_id      bigint generated always as identity primary key,
  customer_id   bigint not null references public.customers (customer_id) on delete restrict,
  order_date    timestamptz not null default now(),
  -- 'pending'   = customer confirmed the order; business has not completed it yet
  -- 'completed' = order fulfilled
  -- 'cancelled' = order cancelled
  status        text not null default 'pending'
                check (status in ('pending', 'completed', 'cancelled')),
  total_amount  numeric(10,2) not null check (total_amount >= 0),
  location      text not null check (length(btrim(location)) > 0),
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create table if not exists public.order_items (
  order_item_id  bigint generated always as identity primary key,
  order_id       bigint not null references public.orders (order_id) on delete cascade,
  product_id     bigint not null references public.products (product_id) on delete restrict,
  quantity       integer not null check (quantity > 0),
  unit_price     numeric(10,2) not null check (unit_price >= 0),  -- price at time of order
  subtotal       numeric(10,2) not null check (subtotal >= 0),
  constraint order_items_subtotal_matches check (subtotal = quantity * unit_price),
  constraint order_items_order_product_unique unique (order_id, product_id)
);

-- ---------------------------------------------------------------
-- Indexes (messenger_id and products.name are already indexed via UNIQUE)
-- ---------------------------------------------------------------
create index if not exists idx_customers_contact_number on public.customers (contact_number);
create index if not exists idx_orders_customer_id       on public.orders (customer_id);
create index if not exists idx_orders_status            on public.orders (status);
create index if not exists idx_orders_order_date        on public.orders (order_date desc);
create index if not exists idx_order_items_order_id     on public.order_items (order_id);
create index if not exists idx_order_items_product_id   on public.order_items (product_id);

-- ---------------------------------------------------------------
-- updated_at triggers
-- ---------------------------------------------------------------
drop trigger if exists trg_products_updated_at on public.products;
create trigger trg_products_updated_at
  before update on public.products
  for each row execute function public.set_updated_at();

drop trigger if exists trg_orders_updated_at on public.orders;
create trigger trg_orders_updated_at
  before update on public.orders
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------
-- Row Level Security: enabled with NO policies, so the anon/authenticated
-- roles cannot read or write. The backend uses the service role key,
-- which bypasses RLS.
-- ---------------------------------------------------------------
alter table public.customers   enable row level security;
alter table public.products    enable row level security;
alter table public.orders      enable row level security;
alter table public.order_items enable row level security;

-- ---------------------------------------------------------------
-- Atomic order creation
-- Customer + order + items are written in ONE transaction (a function call
-- is atomic): if anything fails, nothing is saved.
-- Prices are read from the products table here, so callers cannot set them.
-- p_items format: [{"product_id": 1, "quantity": 2}, ...]
-- The backend must only call this after explicit customer confirmation.
-- ---------------------------------------------------------------
create or replace function public.create_order_with_items(
  p_customer_name   text,
  p_contact_number  text,
  p_messenger_id    text,
  p_location        text,
  p_items           jsonb
)
returns jsonb
language plpgsql
set search_path = public
as $$
declare
  v_customer_id bigint;
  v_order_id    bigint;
  v_total       numeric(10,2);
  v_result      jsonb;
begin
  if p_items is null or jsonb_typeof(p_items) <> 'array' or jsonb_array_length(p_items) = 0 then
    raise exception 'Order must contain at least one item';
  end if;

  if exists (
    select 1
    from jsonb_to_recordset(p_items) as i(product_id bigint, quantity integer)
    where i.product_id is null or i.quantity is null or i.quantity <= 0
  ) then
    raise exception 'Every item needs a product_id and a quantity greater than zero';
  end if;

  if exists (
    select 1
    from jsonb_to_recordset(p_items) as i(product_id bigint, quantity integer)
    left join products p on p.product_id = i.product_id
    where p.product_id is null or p.is_available is not true
  ) then
    raise exception 'One or more products do not exist or are unavailable';
  end if;

  -- Find or create the customer
  if p_messenger_id is not null then
    insert into customers (name, contact_number, messenger_id)
    values (p_customer_name, p_contact_number, p_messenger_id)
    on conflict (messenger_id)
      do update set name = excluded.name, contact_number = excluded.contact_number
    returning customer_id into v_customer_id;
  else
    select customer_id into v_customer_id
    from customers
    where messenger_id is null
      and contact_number = p_contact_number
      and lower(name) = lower(p_customer_name)
    limit 1;

    if v_customer_id is null then
      insert into customers (name, contact_number)
      values (p_customer_name, p_contact_number)
      returning customer_id into v_customer_id;
    end if;
  end if;

  -- Total from authoritative DB prices (duplicate product lines merged)
  select coalesce(sum(p.price * a.qty), 0)
    into v_total
  from (
    select i.product_id, sum(i.quantity) as qty
    from jsonb_to_recordset(p_items) as i(product_id bigint, quantity integer)
    group by i.product_id
  ) a
  join products p on p.product_id = a.product_id;

  insert into orders (customer_id, status, total_amount, location)
  values (v_customer_id, 'pending', v_total, p_location)
  returning order_id into v_order_id;

  insert into order_items (order_id, product_id, quantity, unit_price, subtotal)
  select v_order_id, p.product_id, a.qty, p.price, p.price * a.qty
  from (
    select i.product_id, sum(i.quantity)::integer as qty
    from jsonb_to_recordset(p_items) as i(product_id bigint, quantity integer)
    group by i.product_id
  ) a
  join products p on p.product_id = a.product_id;

  select jsonb_build_object(
    'order_id',     o.order_id,
    'customer_id',  o.customer_id,
    'status',       o.status,
    'total_amount', o.total_amount,
    'location',     o.location,
    'order_date',   o.order_date,
    'items', (
      select coalesce(jsonb_agg(jsonb_build_object(
               'product_id',   oi.product_id,
               'product_name', p.name,
               'quantity',     oi.quantity,
               'unit_price',   oi.unit_price,
               'subtotal',     oi.subtotal
             ) order by oi.order_item_id), '[]'::jsonb)
      from order_items oi
      join products p on p.product_id = oi.product_id
      where oi.order_id = o.order_id
    )
  )
  into v_result
  from orders o
  where o.order_id = v_order_id;

  return v_result;
end;
$$;

-- Only the backend (service role) may call it.
revoke all on function public.create_order_with_items(text, text, text, text, jsonb)
  from public, anon, authenticated;
grant execute on function public.create_order_with_items(text, text, text, text, jsonb)
  to service_role;

-- ---------------------------------------------------------------
-- Seed data: prototype prices (change later with an UPDATE; no code change needed)
-- ---------------------------------------------------------------
insert into public.products (name, description, price) values
  ('Nutella Ferrero Cake', 'Cake with Nutella and Ferrero', 400.00),
  ('Chocolate Moist Cake', 'Moist chocolate cake',           400.00),
  ('Leche Flan',           'Classic leche flan',             400.00),
  ('Mango Float',          'Mango icebox cake / float',      400.00),
  ('Yema Cake',            'Chiffon cake with yema frosting', 400.00)
on conflict (name) do nothing;


create table if not exists public.conversations (
  messenger_id  text primary key,
  state         text not null default 'idle'
                check (state in ('idle', 'collecting_details', 'awaiting_confirmation')),
  draft         jsonb not null default '{}'::jsonb,
  updated_at    timestamptz not null default now()
);

drop trigger if exists trg_conversations_updated_at on public.conversations;
create trigger trg_conversations_updated_at
  before update on public.conversations
  for each row execute function public.set_updated_at();

alter table public.conversations enable row level security;