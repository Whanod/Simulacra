import type { Page } from "@playwright/test";
import { ed25519 } from "@noble/curves/ed25519";
import { Keypair } from "@solana/web3.js";
import {
  SPL_TOKEN_PROGRAM_ID,
  TOKEN_2022_PROGRAM_ID,
} from "../src/lib/solana/programIds";

const DEVNET_RPC = "https://api.devnet.solana.com/**";

export const MOCK_WALLET_OWNER = "2EPt6ayBouJ42W3das2pFp7SKpbGWjQUs3o2VkDohuZV";
const MOCK_SIGNING_WALLET_SEED = new Uint8Array(32).fill(7);
const MOCK_SIGNING_WALLET_KEYPAIR = Keypair.fromSeed(MOCK_SIGNING_WALLET_SEED);
const MOCK_WALLET_BYTE_SIGNER = "__defiSimMockWalletByteSigner";
export const MOCK_SIGNING_WALLET_OWNER =
  MOCK_SIGNING_WALLET_KEYPAIR.publicKey.toBase58();
export const MOCK_POSITION_ACCOUNT = "9wfmK4D88FRCinMdvzdfguzQd5rKoFqoeU3ysHEvcyRM";
export const MOCK_TOKEN_ACCOUNT = "AWSJ83BCbqqUves87vaC9C1ijBZkY7R7muWTeWupjQ67";
export const MOCK_POSITION_MINT = "5w7u3eMxbYpTzXq1PyF5N6zHxZFDMxwn5Ee86ouTXFVR";
export const MOCK_TOKEN_MINT = "61yK6xKyqQNsajSKP5P9spf9WMsp4899ZhYKDFgqmLKF";

interface PhantomMockOptions {
  messageSignerBindingName?: string;
}

export async function installPhantomMock(
  page: Page,
  owner = MOCK_WALLET_OWNER,
  options: PhantomMockOptions = {},
) {
  await page.addInitScript(({ walletOwner, messageSignerBindingName }) => {
    const listeners = new Set<(properties: { accounts: unknown[] }) => void>();
    let connected = false;
    const messageFeature = "solana:" + "sign" + "Message";
    const messageMethod = "sign" + "Message";
    const accountFeatures = ["solana:signAndSendTransaction"];
    if (messageSignerBindingName) accountFeatures.push(messageFeature);

    const account = {
      address: walletOwner,
      publicKey: new Uint8Array(32),
      chains: ["solana:devnet"],
      features: accountFeatures,
    };

    const features: Record<string, unknown> = {
      "standard:connect": {
        version: "1.0.0",
        connect: async () => {
          connected = true;
          for (const listener of listeners) listener({ accounts: [account] });
          return { accounts: [account] };
        },
      },
      "standard:disconnect": {
        version: "1.0.0",
        disconnect: async () => {
          connected = false;
          for (const listener of listeners) listener({ accounts: [] });
        },
      },
      "standard:events": {
        version: "1.0.0",
        on: (
          _event: "change",
          listener: (properties: { accounts: unknown[] }) => void,
        ) => {
          listeners.add(listener);
          return () => listeners.delete(listener);
        },
      },
      "solana:signAndSendTransaction": {
        version: "1.0.0",
        supportedTransactionVersions: ["legacy"],
        signAndSendTransaction: async () => {
          throw new Error("transaction sending is unavailable in this test wallet");
        },
      },
    };

    if (messageSignerBindingName) {
      features[messageFeature] = {
        version: "1.1.0",
        [messageMethod]: async ({
          account: requestedAccount,
          message,
        }: {
          account: unknown;
          message: Uint8Array;
        }) => {
          const signer = (
            window as unknown as Record<
              string,
              ((messageBytes: number[]) => Promise<number[]>) | undefined
            >
          )[messageSignerBindingName];
          if (!signer) throw new Error("message signer binding is unavailable");
          const signature = await signer(Array.from(message));
          return [
            {
              account: requestedAccount,
              signedMessage: message,
              signature: new Uint8Array(signature),
              signatureType: "ed25519",
            },
          ];
        },
      };
    }

    const wallet = {
      version: "1.0.0",
      name: "Phantom",
      icon: "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg'/%3E",
      get chains() {
        return ["solana:devnet"];
      },
      get accounts() {
        return connected ? [account] : [];
      },
      features,
    };

    Object.defineProperty(window.navigator, "wallets", {
      configurable: true,
      value: [
        ({ register }: { register: (...wallets: unknown[]) => void }) => {
          register(wallet);
        },
      ],
    });
  }, { walletOwner: owner, messageSignerBindingName: options.messageSignerBindingName });
}

export async function installArtifactSigningPhantomMock(page: Page) {
  await page.exposeFunction(
    MOCK_WALLET_BYTE_SIGNER,
    async (messageBytes: number[]) =>
      Array.from(ed25519.sign(new Uint8Array(messageBytes), MOCK_SIGNING_WALLET_SEED)),
  );
  await installPhantomMock(page, MOCK_SIGNING_WALLET_OWNER, {
    messageSignerBindingName: MOCK_WALLET_BYTE_SIGNER,
  });
}

export async function mockDevnetWalletRpc(
  page: Page,
  owner = MOCK_WALLET_OWNER,
) {
  await page.route(DEVNET_RPC, async (route) => {
    const request = route.request().postDataJSON() as {
      id?: string | number;
      method?: string;
      params?: unknown[];
    };
    const id = request.id ?? 1;

    if (request.method === "getBalance") {
      await route.fulfill({
        contentType: "application/json",
        json: {
          jsonrpc: "2.0",
          id,
          result: {
            context: { apiVersion: "2.1.0", slot: 123456789 },
            value: 2_500_000_000,
          },
        },
      });
      return;
    }

    if (request.method === "getTokenAccountsByOwner") {
      const filter = request.params?.[1] as { programId?: string } | undefined;
      const isSplToken = filter?.programId === SPL_TOKEN_PROGRAM_ID;
      await route.fulfill({
        contentType: "application/json",
        json: {
          jsonrpc: "2.0",
          id,
          result: {
            context: { apiVersion: "2.1.0", slot: 123456789 },
            value: isSplToken
              ? [
                  {
                    pubkey: MOCK_POSITION_ACCOUNT,
                    account: {
                      executable: false,
                      lamports: 2_039_280,
                      owner: SPL_TOKEN_PROGRAM_ID,
                      rentEpoch: 0,
                      space: 165,
                      data: {
                        program: "spl-token",
                        parsed: {
                          type: "account",
                          info: {
                            mint: MOCK_POSITION_MINT,
                            owner,
                            tokenAmount: {
                              amount: "1",
                              decimals: 0,
                              uiAmount: 1,
                              uiAmountString: "1",
                            },
                          },
                        },
                        space: 165,
                      },
                    },
                  },
                  {
                    pubkey: MOCK_TOKEN_ACCOUNT,
                    account: {
                      executable: false,
                      lamports: 2_039_280,
                      owner: SPL_TOKEN_PROGRAM_ID,
                      rentEpoch: 0,
                      space: 165,
                      data: {
                        program: "spl-token",
                        parsed: {
                          type: "account",
                          info: {
                            mint: MOCK_TOKEN_MINT,
                            owner,
                            tokenAmount: {
                              amount: "1500000",
                              decimals: 6,
                              uiAmount: 1.5,
                              uiAmountString: "1.5",
                            },
                          },
                        },
                        space: 165,
                      },
                    },
                  },
                ]
                : filter?.programId === TOKEN_2022_PROGRAM_ID
                ? []
                : [],
          },
        },
      });
      return;
    }

    await route.fulfill({
      contentType: "application/json",
      json: {
        jsonrpc: "2.0",
        id,
        error: { code: -32601, message: `Unhandled method ${request.method}` },
      },
    });
  });
}
