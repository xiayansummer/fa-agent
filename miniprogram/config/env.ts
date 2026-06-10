export type AppEnv = 'development' | 'staging' | 'production';

export interface AppConfig {
  env: AppEnv;
  apiBase: string;
}

const ENV: AppEnv = 'production';

const CONFIGS: Record<AppEnv, AppConfig> = {
  development: {
    env: 'development',
    apiBase: 'http://127.0.0.1:8000',
  },
  staging: {
    env: 'staging',
    apiBase: 'https://agentapi-staging.investarget.com',
  },
  production: {
    env: 'production',
    apiBase: 'https://agentapi.investarget.com',
  },
};

export const appConfig = CONFIGS[ENV];
