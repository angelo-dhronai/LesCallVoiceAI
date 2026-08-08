--
-- PostgreSQL database dump
--

\restrict O0bvWQa0TLMw1d6rqnc7GlVfgelwMZdUlvm7iEFRG0MZ2PZsS38GbXTtlqDvuES

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Data for Name: phone_numbers; Type: TABLE DATA; Schema: public; Owner: lescall
--

COPY public.phone_numbers (id, number, sig_digits, trunk_id, inbound_agent_slug, owner, direction, label, verified, config_status, last_applied_at, created_at, outbound_agent_slug) FROM stdin;
5	+912264231897	2264231897	6	hardik-inbound	support@milledger.com	both	connected via panel	0	applied	2026-08-01 08:34:24.805736	2026-08-01 08:10:49.874766	hardik-outbound
6	+91 80 3133 9140	8031339140	7	fitcoach_ai-2-inbound	info@craftidea.in	both	connected via panel	0	applied	2026-08-05 10:46:33.796997	2026-08-05 10:46:33.533817	fitcoach_ai-2-outbound
4	+91806595 3160	8065953160	5	maya_lescall_sdr-inbound	angelo@dhronai.com	both	connected via panel	0	applied	2026-07-13 04:28:26	2026-07-13 04:28:26	sen_studio-outbound
\.


--
-- Name: phone_numbers_id_seq; Type: SEQUENCE SET; Schema: public; Owner: lescall
--

SELECT pg_catalog.setval('public.phone_numbers_id_seq', 6, true);


--
-- PostgreSQL database dump complete
--

\unrestrict O0bvWQa0TLMw1d6rqnc7GlVfgelwMZdUlvm7iEFRG0MZ2PZsS38GbXTtlqDvuES

