#ifndef CONDUIT_STREAMER_CRYPTO_AEAD_H
#define CONDUIT_STREAMER_CRYPTO_AEAD_H

#include <windows.h>
#include <bcrypt.h>
#include <stdint.h>
#include <vector>

#pragma comment(lib, "bcrypt.lib")

class CryptoAead {
public:
    CryptoAead();
    ~CryptoAead();

    bool Initialize(const uint8_t* key, size_t key_len);
    void Cleanup();

    // Encrypts plaintext into ciphertext buffer and produces 16-byte auth tag.
    // iv must be 12 bytes. auth_tag must be 16 bytes.
    bool Encrypt(const uint8_t* plaintext, size_t plaintext_len,
                 const uint8_t* aad, size_t aad_len,
                 const uint8_t* iv, size_t iv_len,
                 uint8_t* out_ciphertext,
                 uint8_t* out_auth_tag, size_t auth_tag_len);

    // Decrypts ciphertext and verifies 16-byte auth tag.
    bool Decrypt(const uint8_t* ciphertext, size_t ciphertext_len,
                 const uint8_t* aad, size_t aad_len,
                 const uint8_t* iv, size_t iv_len,
                 const uint8_t* auth_tag, size_t auth_tag_len,
                 uint8_t* out_plaintext);

private:
    BCRYPT_ALG_HANDLE m_alg_handle;
    BCRYPT_KEY_HANDLE m_key_handle;
    std::vector<uint8_t> m_key_object;
};

#endif // CONDUIT_STREAMER_CRYPTO_AEAD_H
