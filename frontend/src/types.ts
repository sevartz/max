export type Tab = 'channels' | 'ads' | 'stats';

export interface Proposal {
  id: number;
  status: string;
  actionable: boolean;
  next_available: boolean;
  post_text: string;
  ad_title: string;
  category: string;
  category_label: string;
  reason: string;
  pricing_model: string;
  pricing_label: string;
  pricing_unit: string;
  price: string;
  expected_income: string;
  expires_at: string;
  publish_at: string | null;
}

export interface ChannelSummary {
  chat_id: number;
  title: string;
  status: string;
  subscribers: number;
  proposal: Proposal | null;
}

export interface CategorySetting {
  code: string;
  label: string;
  regulated: boolean;
  allowed: boolean;
}

export interface ChannelDetail extends ChannelSummary {
  action_result?: 'approved' | 'rejected' | 'category_blocked' | 'next' | 'no_more' | 'stale' | 'expired';
  permissions: string[];
  has_required_permissions: boolean;
  can_delete: boolean;
  profile_summary: string | null;
  blocked_categories: string[];
  allow_regulated: string[];
  ad_ttl_hours: number;
  categories: CategorySetting[];
}

export interface PricingModel {
  code: string;
  label: string;
  unit: string;
}

export interface Limits {
  company_name: number;
  title: number;
  body: number;
  url: number;
}

export interface Bootstrap {
  consented: boolean;
  consent_text?: string;
  consent_version?: string;
  display_name?: string;
  channels: ChannelSummary[];
  advertiser: { exists: boolean; name: string | null; ad_count: number };
  categories: Omit<CategorySetting, 'allowed'>[];
  pricing_models: PricingModel[];
  limits: Limits;
}

export interface Ad {
  id: number;
  title: string;
  body: string;
  url: string;
  category: string;
  category_label: string;
  pricing_model: string;
  pricing_label: string;
  pricing_unit: string;
  price: string;
  status: 'active' | 'paused' | 'exhausted' | string;
  budget_total: string;
  budget_left: string;
  placements: number;
  views: number;
  clicks: number;
  spent: string;
}

export interface AdsData {
  advertiser: { name: string } | null;
  ads: Ad[];
}

export interface ChannelStats {
  placements: number;
  views: number;
  clicks: number;
  earned: string;
}

export interface StatsData {
  channels: Array<{
    chat_id: number;
    title: string | null;
    periods: { '7': ChannelStats; '30': ChannelStats };
  }>;
  advertiser: {
    name: string;
    summary: { placements: number; views: number; clicks: number; spent: string };
    ads: Ad[];
  } | null;
}

export type MiniPage =
  | { type: 'tabs' }
  | { type: 'channel'; id: number }
  | { type: 'ad'; id: number }
  | { type: 'create' }
  | { type: 'edit'; id: number; field: 'title' | 'body' | 'url' | 'category' | 'price' }
  | { type: 'topup'; id: number };
