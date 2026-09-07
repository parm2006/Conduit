#include "crypto_aead.h"

CryptoAead::CryptoAead()
    : m_alg_handle(nullptr)
    , m_key_handle(nullptr)
{
}

CryptoAead::~CryptoAead() {
    Cleanup();
}

void CryptoAead::Cleanup() {
    if (m_key_handle) {
        BCryptDestroyKey(m_key_handle);
        m_key_handle = nullptr;
    }
    if (m_alg_handle) {
        BCryptCloseAlgorithmProvider(m_alg_handle, 0);
        m_alg_handle = nullptr;
    }
    m_key_object.clear();
}

bool CryptoAead::Initialize(const uint8_t* key, size_t key_len) {
    Cleanup();
    if (!key || key_len != 32) { // Require 256-bit key
        return false;
    }

    NTSTATUS status = BCryptOpenAlgorithmProvider(&m_alg_handle, BCRYPT_AES_ALGORITHM, nullptr, 0);
    if (!BCRYPT_SUCCESS(status)) return false;

    status = BCryptSetProperty(m_alg_handle, BCRYPT_CHAINING_MODE,
                               reinterpret_cast<PUCHAR>(const_cast<wchar_t*>(BCRYPT_CHAIN_MODE_GCM)),
                               sizeof(BCRYPT_CHAIN_MODE_GCM), 0);
    if (!BCRYPT_SUCCESS(status)) return false;

    DWORD keyObjSize = 0, resultSize = 0;
    status = BCryptGetProperty(m_alg_handle, BCRYPT_OBJECT_LENGTH,
                               reinterpret_cast<PUCHAR>(&keyObjSize), sizeof(keyObjSize),
                               &resultSize, 0);
    if (!BCRYPT_SUCCESS(status)) return false;

    m_key_object.resize(keyObjSize);
    status = BCryptGenerateSymmetricKey(m_alg_handle, &m_key_handle,
                                       m_key_object.data(), static_cast<ULONG>(m_key_object.size()),
                                       const_cast<PUCHAR>(key), static_cast<ULONG>(key_len), 0);
    return BCRYPT_SUCCESS(status);
}

bool CryptoAead::Encrypt(const uint8_t* plaintext, size_t plaintext_len,
                         const uint8_t* aad, size_t aad_len,
                         const uint8_t* iv, size_t iv_len,
                         uint8_t* out_ciphertext,
                         uint8_t* out_auth_tag, size_t auth_tag_len) {
    if (!m_key_handle || !iv || iv_len != 12 || !out_auth_tag || auth_tag_len != 16) {
        return false;
    }

    BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO authInfo;
    BCRYPT_INIT_AUTH_MODE_INFO(authInfo);
    authInfo.pbNonce = const_cast<PUCHAR>(iv);
    authInfo.cbNonce = static_cast<ULONG>(iv_len);
    authInfo.pbAuthData = const_cast<PUCHAR>(aad);
    authInfo.cbAuthData = static_cast<ULONG>(aad_len);
    authInfo.pbTag = out_auth_tag;
    authInfo.cbTag = static_cast<ULONG>(auth_tag_len);

    ULONG bytesEncrypted = 0;
    NTSTATUS status = BCryptEncrypt(m_key_handle,
                                   const_cast<PUCHAR>(plaintext), static_cast<ULONG>(plaintext_len),
                                   &authInfo,
                                   nullptr, 0,
                                   out_ciphertext, static_cast<ULONG>(plaintext_len),
                                   &bytesEncrypted, 0);
    return BCRYPT_SUCCESS(status);
}

bool CryptoAead::Decrypt(const uint8_t* ciphertext, size_t ciphertext_len,
                         const uint8_t* aad, size_t aad_len,
                         const uint8_t* iv, size_t iv_len,
                         const uint8_t* auth_tag, size_t auth_tag_len,
                         uint8_t* out_plaintext) {
    if (!m_key_handle || !iv || iv_len != 12 || !auth_tag || auth_tag_len != 16) {
        return false;
    }

    BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO authInfo;
    BCRYPT_INIT_AUTH_MODE_INFO(authInfo);
    authInfo.pbNonce = const_cast<PUCHAR>(iv);
    authInfo.cbNonce = static_cast<ULONG>(iv_len);
    authInfo.pbAuthData = const_cast<PUCHAR>(aad);
    authInfo.cbAuthData = static_cast<ULONG>(aad_len);
    authInfo.pbTag = const_cast<PUCHAR>(auth_tag);
    authInfo.cbTag = static_cast<ULONG>(auth_tag_len);

    ULONG bytesDecrypted = 0;
    NTSTATUS status = BCryptDecrypt(m_key_handle,
                                   const_cast<PUCHAR>(ciphertext), static_cast<ULONG>(ciphertext_len),
                                   &authInfo,
                                   nullptr, 0,
                                   out_plaintext, static_cast<ULONG>(ciphertext_len),
                                   &bytesDecrypted, 0);
    return BCRYPT_SUCCESS(status);
}
